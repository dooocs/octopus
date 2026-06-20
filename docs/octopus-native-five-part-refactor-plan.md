# Octopus Native Five-Part Refactor Plan

本文档记录当前强迁移后的目标和验收口径：运行入口只接受五段式 `input`，所有来源都通过 `sources/*` adapter 执行，不再保留 legacy flat engine 兼容层。

## 1. 当前状态

已经完成：

- 运行配置只接受顶层 `scraper` + `input`。
- `input` 必须包含且只包含五个一级 key：`source`、`fetch`、`filters`、`enrich`、`runtime`。
- `core.runner` 执行链路为 `discover -> enrich -> select -> normalize`。
- `sources/*` adapter 直接读取 `config.input.*`。
- `sources/legacy.py`、`LegacyEngineAdapter`、`flat_config()`、旧 `scrapers/*Engine` 文件已经删除。
- `RawItem` 只接受 `item_type`，不再接受 `content_type`。
- `infra.models` 只导出 `RawItem`，不再提供 `BaseScraper` shim。
- Web admin specs 由 Python `ChannelSpec` 生成到 `web/src/generated/scraper_specs.json`。

仍需单独处理：

- `items_filtered` 仍是现有日志/任务字段名；当前代码中它表示最终 selected 数量。后续若改表结构，可以重命名为 `items_selected`。
- `scripts/migrate_scraper_configs_v1.py` 可作为一次性历史迁移工具保留，但不得被 runner 导入，也不得成为运行时兼容路径。

## 2. 执行模型

固定运行链路：

```text
load configs
  -> validate config
  -> discover
  -> enrich
  -> select
  -> normalize
  -> validate output
  -> sink
  -> update run/task/state
```

阶段边界：

| 阶段 | 输入配置 | 职责 |
| --- | --- | --- |
| `discover` | `source` + `fetch` + 必要的 `filters` + `runtime` | 从来源侧召回候选。允许使用来源原生 query、score、时间窗口、topic 等条件减少请求量和解析量。 |
| `enrich` | `enrich` + `runtime` | 二阶段补充 discover 没拿到或不适合在列表页拿的详情，例如正文、README、评论、点击数、语言、图片。 |
| `select` | `filters` + 已 enrich 的记录 | 基于已知质量信号做最终保留、排序、截断。 |
| `normalize` | 顶层配置 + adapter 代码 | 转换成稳定 `RawItem`。 |

这里的 `filters` 是配置域，不是固定执行阶段。它可以被 `discover` 用于来源侧剪枝，也可以被 `select` 用于最终筛选。判断标准是成本和信息可得性。

## 3. 关键设计结论

### Discover 可以做召回剪枝

例如 HN `min_score`、GitHub query 中的 `stars:>100`、Product Hunt `min_votes`，如果来源 API 或列表数据已经提供了这些信号，就应该尽早用。这样可以减少二阶段请求、降低来源负载，也符合“这个网站抓精品而不是全量抓取”的定位。

这类剪枝不是旧意义上的最终 filter，它的目标是控制召回规模和成本。

### Enrich 后再做最终 select

最终质量判断应该发生在 `enrich` 后，因为很多质量信号只有二阶段才完整：

- V2EX 点击数需要 topic 页面。
- LinuxDo 回复详情需要 topic 页面。
- README、评论、正文会影响内容质量判断。
- release body/assets 如果首轮 API 已经返回，就已经属于可用详情，不需要为了形式强行再 enrich。

因此结论是：`discover` 可以按来源能力提前剪枝，但最终保留/排序/截断统一放在 `select`，并且 `select` 位于 `enrich` 之后。

### Enrich 不要求重复请求

`enrich` 的目的不是机械地发第二次请求，而是补齐 discover 阶段没有拿到的内容。如果某个来源的列表/API 响应已经包含最终需要的 body、assets、metadata，adapter 可以让 `enrich` no-op，把这些字段直接带入 `SourceRecord`。

## 4. 配置契约

Supabase `octp_scraper_configs` 的运行输入必须是：

```json
{
  "scraper": "hackernews",
  "input": {
    "source": {},
    "fetch": {},
    "filters": {},
    "enrich": [],
    "runtime": {}
  }
}
```

禁止运行时 fallback：

- `type`
- `config`
- flat dict 字段，如顶层 `min_score`、`url`、`fetch_full_text`
- `flat_config()`

## 5. 代码分层

| 层 | 职责 |
| --- | --- |
| `core` | 契约、adapter registry、runner、校验。 |
| `sources` | source-specific 请求、解析、enrich、select、normalize。 |
| `infra` | HTTP、DAO、OSS、secret 等基础能力。 |
| `pipeline` | sink 等跨来源流水线能力。 |
| `scripts` | Action/CLI 入口、配置读取、spec 导出、一次性迁移工具。 |
| `web` | 管理配置和运行态，不手写 source schema。 |

## 6. 验收门禁

运行时代码不应命中：

```bash
rg -n 'LegacyEngineAdapter|flat_config|BaseScraper|row\.get\("config"|row\.get\("type"|from scrapers|import scrapers' core infra sources scripts tests
```

`content_type` 不允许作为 `RawItem` 字段或 scraper 配置字段存在：

```bash
rg -n 'content_type' core sources scripts tests
```

必须通过：

```bash
python -m scripts.export_scraper_specs
python scripts/export_scraper_specs.py
python -m compileall core infra pipeline sources scripts tests
python -m unittest discover tests
cd web && npm run build
```

## 7. 完成定义

只有同时满足以下条件，才算“全部原生五段式”完成：

- 所有 adapter 直接读取 `config.input.*`。
- runner 不接受旧 `type` / `config` 字段。
- 运行时代码不存在 legacy flat bridge、`BaseScraper`、旧 `scrapers/*Engine`。
- `RawItem` 只接受 `item_type`。
- `core.runner` 执行 `discover -> enrich -> select -> normalize`。
- Python tests 和 web build 通过。
- `Global Scrape` 在目标分支上真实跑通。
