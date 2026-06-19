# Octopus Crawler Foundation Architecture

本文档是 Octopus 后续重构的技术依据。目标是把当前“多个可运行 scraper 的集合”升级为“代码 + 配置驱动的爬虫基础层”，为后续索引、摘要、排序、展示、向量化等消费链路提供稳定数据。

## 1. 核心结论

Octopus 的边界分三层：

| 层级 | 是否配置化 | 说明 |
| --- | --- | --- |
| 抓什么 | 是 | 来源、关键词、账号、榜单、时间窗口、分页、数量限制由配置控制。 |
| 筛什么 | 是 | GitHub stars、HN score、Product Hunt votes、Reddit NSFW 等由渠道配置控制。 |
| 最终保存什么字段 | 否 | `raw_items` 是代码级稳定契约，不允许由配置决定字段名、字段集合或表结构。 |

一句话原则：

> 配置决定候选集和补充动作，代码决定最终输出契约。

因此不要设计 `output_fields`、`save_content`、`save_metrics` 这类配置。否则下游会面对不确定的数据结构，后续消费链路会被配置污染。

## 2. 目标架构

固定执行链路：

```text
load configs
  -> validate config
  -> discover
  -> filter
  -> enrich
  -> normalize
  -> validate output
  -> sink
  -> update run/task/state
```

配置五段式：

```json
{
  "source": {},
  "fetch": {},
  "filters": {},
  "enrich": [],
  "runtime": {}
}
```

二者关系：

| 执行阶段 | 读取配置 | 职责 |
| --- | --- | --- |
| `discover` | `source` + `fetch` + `runtime` | 访问来源，拿到候选记录。 |
| `filter` | `filters` | 按渠道原生语义过滤候选记录。 |
| `enrich` | `enrich` + `runtime` | 二阶段补充正文、README、评论、图片等。 |
| `normalize` | 顶层配置 + adapter 代码 | 转换成统一 `RawItem`。 |
| `validate output` | `RawItem` contract | 校验最终输出是否满足 `raw_items` 契约。 |
| `sink` | runner/env/CLI | 写 JSONL、RDS、日志、状态；不属于单个 scraper 的 `input`。 |

`runtime` 是横切配置，不是独立业务阶段。`sink` 是运行器能力，不进入 scraper 配置。

## 3. 配置契约

Supabase `octp_scraper_configs` 顶层字段保持稳定：

```json
{
  "name": "GitHub AI Search",
  "scraper": "github_search",
  "enabled": true,
  "priority": 20,
  "source_type": "code_host",
  "sub_source_type": "github_ai_search",
  "item_type": "repo",
  "input_schema_version": 1,
  "input": {
    "source": {},
    "fetch": {},
    "filters": {},
    "enrich": [],
    "runtime": {}
  }
}
```

顶层字段含义：

| 字段 | 说明 |
| --- | --- |
| `scraper` | 代码适配器 ID，必须能在 adapter registry 中找到。 |
| `source_type` | 来源大类，如 `website`、`social`、`community`、`code_host`、`model_hub`、`product_platform`。 |
| `sub_source_type` | 具体业务通道 ID，必须稳定且唯一，如 `github_ai_search`、`hackernews_new`。 |
| `item_type` | 内容对象类型，如 `article`、`repo`、`post`、`discussion`、`paper`、`model`、`product`。 |
| `input` | 五段式渠道参数。 |

五段式 `input` 的一级 key 必须一致，内部字段由每个 adapter 的 `ChannelSpec.input_schema` 决定：

| key | 说明 |
| --- | --- |
| `source` | 源头定义，例如 RSS URL、GitHub queries、Reddit subreddit、X accounts。 |
| `fetch` | 抓取范围，例如 `limit`、`per_page`、`window_days`、`cutoff_hours`、`top_n`。 |
| `filters` | 过滤条件，保留渠道原生语义，例如 `min_stars`、`min_score`、`min_votes`。 |
| `enrich` | 二阶段补充动作列表，例如 `github_readme`、`article_body`、`top_comments`。 |
| `runtime` | 执行控制，例如 `timeout`、`retries`、`concurrency`、`rate_limit`。 |

错误示例：

```json
{
  "input": {
    "min_popularity": 100
  }
}
```

不要用 `min_popularity` 强行抹平渠道差异。GitHub stars、HN score、Product Hunt votes 是不同语义，应保留原生字段。

正确示例：

```json
{
  "input": {
    "source": {
      "queries": [{ "q": "topic:ai stars:>100", "label": "ai" }]
    },
    "fetch": {
      "per_page": 30,
      "window_days": 7
    },
    "filters": {
      "min_stars": 100
    },
    "enrich": [
      { "name": "github_readme", "when": "always" }
    ],
    "runtime": {
      "timeout": 15,
      "retries": 3,
      "concurrency": 4
    }
  }
}
```

HackerNews 示例：

```json
{
  "input": {
    "source": {
      "feed": "newstories"
    },
    "fetch": {
      "new_n": 500,
      "cutoff_hours": 36
    },
    "filters": {
      "min_score": 50
    },
    "enrich": [
      { "name": "article_body", "when": "has_external_url" }
    ],
    "runtime": {
      "timeout": 10,
      "retries": 2,
      "concurrency": 5
    }
  }
}
```

## 4. 输出契约

`raw_items` 是最终稳定边界。它只表达“抓到了什么内容”，不表达“怎么抓到的”。

固定字段：

```text
id
url
source_type
sub_source_type
item_type
author_id
author_url
created_date
updated_date
source_published_date
snapshot_date
title
metrics
content
context_content
extra
scrape_config_snapshot
```

字段规则：

| 字段 | 规则 |
| --- | --- |
| `content` | 主内容。下游默认索引、摘要、向量化时优先消费。 |
| `context_content` | 主内容之外的结构化上下文，例如评论、thread、README 辅助信息、makers、图片。 |
| `metrics` | 来源原生指标，保留原字段名，不做跨渠道归一。 |
| `extra` | 来源特定追溯字段，例如 native ID、feed URL、topic、external URL。 |
| `scrape_config_snapshot` | 抓取时配置快照，用于追溯。 |

`metrics` 示例：

```json
// GitHub
{ "stars": 1200, "forks": 80, "watchers": 30 }

// HackerNews
{ "score": 350, "comments": 42 }

// Product Hunt
{ "votes": 210, "comments": 19 }
```

如果未来需要跨渠道排序，不在爬虫基础层强行生成 `normalized_score`。应在后续 ranking/processing pipeline 中基于 `metrics`、时间、来源权重独立计算。

ID 生成规则：

```text
md5(source_type + ":" + sub_source_type + ":" + identity)
```

`identity` 优先使用来源稳定 native ID；没有 native ID 时 fallback 到 URL。native ID 应进入 `extra.native_id` 或 adapter 内部标准字段，便于追溯。

## 5. 代码分层

目标目录结构：

```text
octopus/
  core/
    contracts.py
    registry.py
    runner.py
    validation.py
    filters.py
  infra/
    http.py
    dao/
    object_storage.py
    secrets.py
  sources/
    github/
    hackernews/
    rss/
    twitter/
    reddit/
    product_hunt/
    huggingface/
    community/
  pipeline/
    enrichers.py
    sinks.py
  scripts/
    global_scrape.py
    export_scraper_specs.py
  web/src/generated/
    scraper_specs.json
```

分层规则：

| 层 | 允许做什么 | 禁止做什么 |
| --- | --- | --- |
| `core` | 定义契约、注册、编排、校验、通用过滤。 | 写渠道 HTTP 细节、写 DB 细节。 |
| `infra` | HTTP、DB、OSS、secret、外部服务基础能力。 | 了解业务 item_type 或具体来源语义。 |
| `sources` | 渠道访问、解析、字段映射。 | 直接写 RDS、直接写 Supabase logs。 |
| `pipeline` | enrich 和 sink 的可复用阶段。 | 内嵌具体渠道抓取入口。 |
| `web` | 管理配置和展示运行态。 | 手写渠道 schema 源。 |

## 6. Adapter 与 ChannelSpec

所有渠道实现同一个 adapter 接口：

```python
class SourceAdapter:
    spec: ChannelSpec

    def discover(self, ctx: RunContext, config: RuntimeConfig) -> list[SourceRecord]:
        ...

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: RuntimeConfig,
    ) -> list[SourceRecord]:
        return records

    def normalize(
        self,
        ctx: RunContext,
        record: SourceRecord,
        config: RuntimeConfig,
    ) -> RawItem:
        ...
```

`discover` 返回来源中间记录，不直接返回最终表字段。`normalize` 才能生成 `RawItem`。

`ChannelSpec` 是单一 schema 源：

```python
@dataclass(frozen=True)
class ChannelSpec:
    scraper: str
    label: str
    group: str
    default_source_type: str
    default_item_type: str
    input_schema_version: int
    input_schema: dict[str, Any]
    default_input: dict[str, Any]
    required_secrets: list[str]
    supported_enrichers: list[str]
    rate_limit: dict[str, Any]
```

规则：

- Python `ChannelSpec` 是唯一权威来源。
- 前端 `scraper_specs.json` 由 `scripts/export_scraper_specs.py` 生成。
- 管理端禁止创建没有 spec 的 scraper。
- 现有 `web/src/lib/channels.ts` 不再维护渠道默认配置。

## 7. 运行态数据

现有 `octp_scraper_logs` 不足以表达全局批次、阶段状态和 cursor。强迁移时新增三类表。

### `octp_scrape_runs`

记录一次全局运行：

```text
id
snapshot_date
trigger_type
trigger_ref
status
started_at
finished_at
summary
created_date
updated_date
```

`status` 建议枚举：

```text
running
success
partial
failed
cancelled
```

### `octp_scrape_tasks`

记录一次 run 下某个 config 的执行结果：

```text
id
run_id
scraper_config_id
snapshot_date
scraper
sub_source_type
status
stage
config_snapshot
items_discovered
items_filtered
items_enriched
items_written
duration_ms
error_message
error_logs
created_date
updated_date
```

`stage` 建议枚举：

```text
validate_config
discover
filter
enrich
normalize
validate_output
sink
done
```

单个 task 失败不应中断整个 run，除非 runner 显式启用 fail-fast。

### `octp_scraper_state`

记录每个 config 的增量状态：

```text
scraper_config_id
state
last_success_snapshot_date
last_success_run_id
created_date
updated_date
```

`state` 是 JSON，例如 cursor、last seen id、last fetched timestamp。state 不进入 `raw_items`。

## 8. 现有渠道迁移要求

全量迁移当前渠道：

| 当前 scraper | 目标 adapter | 关键迁移点 |
| --- | --- | --- |
| `rss` | `sources/rss` | `url` 进入 `source`，窗口和数量进入 `fetch`。 |
| `ai_blog` | `sources/website_blog` | selector 进入 `source`，窗口进入 `fetch`。 |
| `github_trending` | `sources/github` | README、语言、图片、star history 进入 `enrich`。 |
| `github_search` | `sources/github` | queries 进入 `source`，stars 过滤进入 `filters`。 |
| `hackernews` | `sources/hackernews` | newstories 进入 `source`，score 进入 `filters`，正文进入 `enrich`。 |
| `twitter_twscrape` | `sources/twitter` | accounts/keywords 进入 `source`，likes 进入 `filters`。 |
| `community_v2ex` | `sources/community` | 热榜抓取进入 `discover`，回复补充进入 `enrich`。 |
| `community_linuxdo` | `sources/community` | 热榜抓取进入 `discover`，回复补充进入 `enrich`。 |
| `reddit` | `sources/reddit` | subreddit 进入 `source`，NSFW/score 进入 `filters`。 |
| `hf_model` | `sources/huggingface` | downloads/likes 进入 `filters`。 |
| `hf_papers` | `sources/huggingface` | top_n 进入 `fetch`。 |
| `product_hunt` | `sources/product_hunt` | votes/topic 白黑名单进入 `filters`。 |

如果某个 UI 渠道没有对应 adapter，例如旧 `twitter_nitter`，不得进入新 spec；要么补齐 adapter，要么从 UI 和配置迁移中移除。

## 9. 迁移策略

本次采用强迁移：

1. 新增 `input_schema_version`、run/task/state 表。
2. 引入 `core` 契约、adapter registry、ChannelSpec。
3. 写配置迁移脚本，把所有现有 flat input 一次性转换为五段式 input。
4. 迁移所有现有 scraper 到新 adapter 接口。
5. runner 不保留旧 flat input 运行兼容。
6. 前端改为读取 generated specs。
7. `octp_scraper_logs` 停止作为新运行器写入目标，仅保留历史数据。

迁移脚本必须输出：

```text
converted configs count
failed configs count
failed config id/name/reason
unsupported scraper configs
```

只要有配置无法迁移或无法通过新 schema 校验，迁移不得静默成功。

## 10. 校验与测试

必须覆盖：

| 类型 | 场景 |
| --- | --- |
| 配置校验 | 缺少五段 key、多余一级 key、字段类型错误、未知 enrich name。 |
| spec 校验 | registry 中每个 adapter 都有合法 `ChannelSpec`。 |
| 输出校验 | `RawItem` 必填字段、JSON 字段、ID、时间字段满足 `raw_items` 契约。 |
| runner | 单 task 失败隔离、run summary 正确、state 更新正确。 |
| adapter fixture | 每个 adapter 用固定 fixture 验证 discover/enrich/normalize。 |
| 迁移脚本 | flat input 到五段式 input 的转换结果可重复、可校验。 |
| 前端 | generated specs 能加载，无法创建未知 scraper。 |

建议验证命令：

```bash
python -m compileall infra scrapers scripts tests
python -m unittest discover tests
cd web && npm run build
```

## 11. 验收标准

重构完成必须满足：

- 所有 enabled config 的 `input` 都是五段式结构。
- 所有 scraper 都通过 adapter 接口运行。
- Python `ChannelSpec` 是唯一渠道 schema 来源。
- 前端不再手写渠道默认配置。
- 新 runner 写入 run/task/state。
- `raw_items` 表结构不被配置化，也不新增运行过程字段。
- 每个渠道输出仍满足现有 `raw_items` contract。
- 单个渠道失败不会污染其他渠道结果。
- 下游可以只依赖 `raw_items` 继续消费数据。

