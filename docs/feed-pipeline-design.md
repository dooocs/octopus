# 动态（博主时间线）链路与加工层设计

状态：待确认（2026-10-16）。确认后按第 9 节的顺序分 PR 实现。

本文覆盖两件事：

1. 蜗牛杂货站新增的「动态」tab：按小时抓取指定博主的原创推文，AI 加工后按博主聚合展示。
2. 借这条新链路，在 Octopus 里建立**加工层**（以后逐步替代 ahaIndexSync 的加工逻辑），并让采集配置和加工流程在同一个后台里管理，删除或停用采集配置时能看到依赖关系。

## 1. 需求（已确认）

| 项 | 结论 |
| --- | --- |
| 博主名单 | 沿用「X AI Leaders」现有的 22 个账号，后台可改 |
| 抓取时间 | 北京时间 06:00 到次日 01:00 每小时一次（每天 20 次），01:00–06:00 不抓；06:00 那次补抓 01:00–06:00 |
| 抓取范围 | 只要原创：排除转推、回复他人、引用推；博主自己连发的长 thread 保留并合并 |
| 存储 | 文字落库，图片、视频和 GIF 全部转存 OSS；不保存 X 的原始媒体链接（2026-10-20） |
| 加工 | 必须 AI 加工：只做中文翻译（2026-10-17 去掉了博主 24 小时摘要） |
| 展示 | 只展示滚动 24 小时；按博主合并成卡片，卡片按该博主最新一条的时间倒序；每条帖子先显示原文，下方显示中文译文（`text_zh` 为空则只显示原文）；导航位置在「AI 日报」与「专题」之间 |
| 代码归属 | 采集和加工都在 Octopus；展示和发布在 ahaIndex2 |

## 2. 现状（2026-10-16 核对）

- Twitter 适配器 `twitter_twscrape` 走 twitterapi.io（`TWITTERAPI_IO_KEY`），按账号调用 `/twitter/user/last_tweets`。
- **静默失败**：`TwitterAdapter.discover()` 捕获所有异常并返回空列表。余额耗尽后接口报错被当成「没有新推文」。ahaIndexSync 里的 "X (Twitter)" 源最后一次有数据是 2026-07-08（35 条），之后 95 次运行都是 `success / 0 条`。
- 配置和运行表（`octp_*`）在 Supabase「aha index」项目里；名为「octopus」的 Supabase 项目已暂停，不影响运行。
- `octp_scraper_state` 已存在，按配置保存 JSON 状态，成功后由 runtime 写回，可直接用作抓取游标。
- `raw_items` 在阿里云 RDS MySQL，主键 `md5(url)`，写入是 upsert，天然幂等。
- Global Scrape 只能手动触发，最后一次运行是 2026-06-13。

## 3. 分层与边界

```text
采集层 crawler/          加工层 processing/                 发布 (ahaIndex2)
discover→prune→enrich    读 raw_items                       读加工产出
→normalize               → 线程合并 → 翻译                    → 渲染 /feed
写 raw_items (RDS)       写加工产出表 (Supabase)             → 只上传 /feed 相关文件
图片 → OSS media/        记录 run / 成本                      → 刷新 CDN
```

规则：

- `raw_items` 仍是采集层唯一的输出契约。采集层只写 `raw_items` 和 OSS 素材；加工层只读 `raw_items`，写自己的产出表。
- `crawler/` 与 `processing/` 两个包不互相 import。共享的只有数据契约，以及 `infra/` 里的网关（RDS、Supabase、OSS、LLM）。
- 加工产出写 Supabase「aha index」项目。网站构建本来就读这里，sync 现有的加工产出也在这里，以后迁移日报时不需要换库。
- 以后对外开放数据，开放的是 `raw_items`，不会带上加工结果。

## 4. 采集：`twitter_feed` 适配器

新增适配器（新的 scraper 类型，不改日报在用的 `twitter_twscrape`），并新建一条配置：

| 字段 | 值 |
| --- | --- |
| name | 动态 · X 博主 |
| scraper | `twitter_feed` |
| source_type / item_type | `social` / `post` |
| sub_source_type | `x_feed_accounts` |
| input.source.watch_accounts | 从「X AI Leaders」复制的 22 个账号（两份名单以后可以各自改） |
| input.fetch | `max_lookback_hours: 24`、`overlap_minutes: 5` |
| input.enrich | `[{ "name": "media_archive" }]` |

### 4.1 时间窗口：游标，不按墙上时间

```text
since = state.feed_cursor - overlap_minutes      （没有游标时：now - max_lookback_hours）
until = now
成功后 state.feed_cursor = until
```

- 正常情况下与需求一致：06:00 那次的游标是 01:00 那次留下的，所以抓的是 01:00–06:00；其余每次抓一小时。
- GitHub 定时任务延迟或跳过一次时不会漏抓。窗口重叠 5 分钟带来的重复数据，由 `raw_items` 主键去重。
- 只有任务成功才推进游标。失败的那次，下次运行会把它的窗口一起补上。

### 4.2 查询：合并账号、按窗口搜索

```text
GET /twitter/tweet/advanced_search
query = (from:a OR from:b OR …) since_time:<since> until_time:<until>
queryType = Latest，翻页直到 has_next_page = false
```

- 账号按查询长度分批，22 个账号预计 1～2 个查询。
- 计费：twitterapi.io 按返回的推文计费，每 1000 条 0.15 美元，每次请求最低收 0.00015 美元。现在的「每个账号拉一页 `last_tweets`」，不管有没有新推都按整页计费。按 20 次/天估算，现在的做法约 40 美元/月，合并后约几美元/月。

### 4.3 过滤与 thread

在 `prune` 阶段完成，依据接口返回的字段：

| 类型 | 判断 | 处理 |
| --- | --- | --- |
| 转推 | 有被转推的原推 | 丢弃 |
| 引用推 | 有被引用的推文 | 丢弃 |
| 回复他人 | 是回复，且回复对象不是作者本人 | 丢弃 |
| 回复自己（thread） | 是回复，且回复对象是作者本人 | 保留，标记所属 thread |
| 原创 | 其余情况 | 保留 |

- 每条推文仍然各自写成一个 `raw_item`（原子化存储）。thread 信息写进 `context_content.thread = { conversation_id, root_id, is_root }`，**合并放在加工层做**。
- thread 的第一条如果早于本次窗口，加工层会从 `raw_items` 里找；找不到时，再按 id 补拉一次第一条。

**待真实数据核对（充值后第一件事）**：接口里表示转推、引用、回复对象、会话 id、媒体的具体字段名；`-filter:replies` 这类操作符能否在服务端生效（能的话可以少付回复的钱）；OR 查询的长度上限。

### 4.4 图片转存（`media_archive` enricher）

- 下载照片和视频封面，以及博主头像。按内容的 sha256 命名，写到 `oss://amazingindex/media/x/<sha256>.<ext>`，通过 `https://www.amazingindex.com/media/x/…` 访问。
- 视频和 GIF（2026-10-20）：从 `video_info.variants` 取短边不超过 720 的最高码率 mp4，按 X 的 `media_key` 命名写到 `media/x/video/<media_key>.mp4`；对象已存在就不再下载（每小时窗口有重叠）。单个上限 50MB，超过的只留封面并标 `video_too_large`。
- 写入 `context_content.media = [{ type, url, key, width, height, video_url?, video_key?, video_too_large?, duration_ms? }]`（`url` 是图片或视频封面）；头像写到 `extra.avatar_url`。
- **不保存 X 的原始链接**：`source_url`、`video_source_url`、`avatar_source_url` 只在 enrich 期间存在，`normalize` 统一剥掉；没转存成功的媒体直接丢弃。存量数据由 `scripts/backfill_feed_media.py`（workflow「Feed Media Backfill」）清理并补存视频。
- 内容相同的图片只存一份。单张图片下载失败只记录警告，不让整个任务失败；推文本身照常写入，不带这张图。
- ahaIndex2 全量构建的清理只覆盖固定的几个前缀，不包含 `media/`，所以这些图片不会被误删。

### 4.5 修复静默失败（同时修日报在用的适配器）

- 401、402、403 以及「余额不足」类的响应，抛出明确的异常，并且**不再被 `discover()` 吞掉**。
- 任务状态记为 `failed` 并写入错误信息，workflow 退出码非 0，GitHub 会发失败通知。
- 「0 条」只在接口明确返回空列表时才算正常。
- ahaIndexSync 里的同类问题要在那个仓库单独修。迁移完成之前，日报还在用它。

### 4.6 调度

新建 workflow `feed_collect.yml`（与 Global Scrape 分开）：

```yaml
on:
  schedule:
    - cron: "0 22,23,0-17 * * *"   # UTC，对应北京时间 06:00–次日 01:00
  workflow_dispatch: {}
concurrency:
  group: feed-pipeline
  cancel-in-progress: false
```

同一个 job 里依次执行：只跑 `x_feed_accounts` 这一条配置的采集 → 动态加工流程 → 通知 ahaIndex2 发布。任何一步失败，后面的步骤都不执行。

## 5. 加工层（`processing/`）

### 5.1 通用框架

加工流程（pipeline）是代码加配置：代码决定有哪些步骤、产出什么结构，配置决定读哪些数据、用哪个模型、提示词版本、是否启用。这与采集层「代码决定输出契约」的原则一致。

```text
load pipeline config → select inputs → steps… → write outputs → record run (status, counts, cost)
```

- **输入用选择器（软依赖）**：`input.selector = { source_type, sub_source_types[], window_hours }`。删掉一个博主或一条配置，结果只是输入变少，流程不会报错。
- **增量与缓存**：每个 LLM 步骤按 `(raw_item_id, 内容哈希, prompt_version)` 缓存。每小时重跑只处理新内容；改提示词时升级 `prompt_version`，就会重新生成。
- **可观测**：每次运行记录读入多少条、新加工多少条、命中多少缓存、LLM token 数和估算成本、错误。

### 5.2 动态流程 `feed_digest`

| 步骤 | 做什么 |
| --- | --- |
| select | 读取 `x_feed_accounts`、发布时间在最近 24 小时内的 `raw_items` |
| thread_merge | 按 `conversation_id` 合并同一作者的 thread，以第一条的时间为准 |
| translate | 每条或每个 thread 生成忠实的中文翻译（Kimi，`KIMI_API_KEY`），段落与原文一一对应，保留原文。原文已是中文、或去掉链接和 @ 后没有文字的帖子不调用模型，`text_zh` 存空串 |
| write | 写入 `proc_feed_posts`（带博主昵称和头像）；单条翻译失败不影响其他帖子，失败的这条不写入、下次重试，本次运行记为 partial 并让 workflow 失败；超出 24 小时的数据保留在表里，只是不再展示 |
| notify | 有变化时向 ahaIndex2 发送 `repository_dispatch: feed-updated` |

## 6. 表结构（Supabase「aha index」）

```sql
-- 加工流程定义
create table public.octp_pipelines (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,               -- feed_digest
  name text not null,
  kind text not null,                      -- 代码里注册的流程类型
  enabled boolean not null default true,
  input jsonb not null default '{}'::jsonb,   -- selector、model、prompt_version …
  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

-- 硬依赖：流程必须引用的具体采集配置。删除被引用的配置会被数据库拒绝。
create table public.octp_pipeline_sources (
  pipeline_id uuid not null references public.octp_pipelines(id) on delete cascade,
  scraper_config_id uuid not null references public.octp_scraper_configs(id) on delete restrict,
  primary key (pipeline_id, scraper_config_id)
);

create table public.octp_pipeline_runs (
  id uuid primary key default gen_random_uuid(),
  pipeline_id uuid references public.octp_pipelines(id) on delete set null,
  status text not null check (status in ('running','success','partial','failed')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  summary jsonb not null default '{}'::jsonb,   -- 读入/新加工/缓存/tokens/成本
  error_message text
);

-- 动态产出：一条推文或一个 thread 一行（博主卡片由网站按 author 分组得到）
create table public.proc_feed_posts (
  id text primary key,                     -- thread 第一条的 raw_item id
  raw_item_ids text[] not null,
  author text not null,                    -- X handle
  display_name text,                       -- 博主昵称（取 thread 最新一条）
  avatar_url text,                         -- 头像（OSS）
  published_at timestamptz not null,       -- thread 第一条的时间
  updated_at timestamptz not null,         -- thread 最后一条的时间
  url text not null,
  text_original text not null,
  text_zh text not null,                   -- '' = 无需翻译，只显示原文
  media jsonb not null default '[]'::jsonb,
  content_hash text not null,
  prompt_version int not null,
  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);
create index on public.proc_feed_posts (author, updated_at desc);
```

采集配置改为默认停用，而不是删除：`octp_scraper_configs` 增加 `archived_at timestamptz`。后台的「删除」先检查 `octp_pipeline_sources` 里有没有引用：有引用就提示「被 X 流程引用」，只允许停用；没有引用才允许归档。

## 7. 后台（`web/`）

- 博主名单：直接在现有配置弹窗里编辑「动态 · X 博主」的 `watch_accounts`，不需要新做页面。
- 新增「加工流程」页签：列出各个流程、读取哪些数据（选择器）、硬依赖哪些采集配置、最近几次运行的状态和成本，可以启用或停用。
- 采集配置的停用和删除按上一节的规则执行，并显示「被哪些流程引用」。

## 8. 发布（ahaIndex2）

2026-10-17 实现时调整：`/feed` 是普通的 Astro 静态页（`src/pages/feed.astro`），构建时读 `proc_feed_posts` 直接渲染，而不是 shell + `fillSlots`（`fillSlots` 不能对已经填过的页面再填一次，每小时更新做不了）。

- 新 workflow `feed-deploy.yml`：由 Feed Collect 结束时的 `repository_dispatch: feed-updated` 触发（需要 octopus 有 `GH_PAT`），另有北京时间 06:40–01:40 每小时一次的定时兜底。独立并发组 `feed-deploy`，不会挤掉排队中的日报部署。
- `run-deploy.sh` 的 `feed` 模式：`BUILD_MODE=feed` 构建（不渲染任何日报、文章、项目页），只上传 `feed/index.html`、它的无斜杠副本和 OSS 上还没有的 `_astro` 资源，只刷新 `/feed` 两个 CDN 地址。日报和全量构建也会顺带重建 `/feed`。
- 页面：按博主分卡片（卡片按最新一条倒序），每条帖子原文在上、`text_zh` 在下（为空只显示原文），图片和视频封面只用 OSS 上的副本，时间为北京时间。导航在「AI 日报」与「专题」之间；旧的日报页要跑一次全量构建才会出现新导航。
- 读取权限：`sql/013` 给 anon 开放 `proc_feed_posts` 只读（网站构建可能用 publishable key；内容本来就公开）。
- SEO：只有 `/feed` 这一个聚合页，进 sitemap，不给每条推文单独生成页面。

## 9. 实施顺序

1. **PR 1 采集**：修复静默失败；新增 `twitter_feed` 适配器、`media_archive` enricher、游标窗口逻辑；新建 `feed_collect.yml`（先只跑采集）。充值后先用真实数据核对 4.3 里列出的字段。
2. **PR 2 加工层**：`processing/` 框架；第 6 节的表；`feed_digest` 流程；运行记录和成本统计。
3. **PR 3 后台**：「加工流程」页签；依赖检查；停用和归档。
4. **PR 4 ahaIndex2**：`/feed` 页面、独立发布流程、导航 tab；octopus 加工完成后发 `feed-updated`。
5. 之后：把日报的打分、摘要、排序逐个从 sync 迁到加工层，每迁一个就关掉 sync 里对应的那一段。

## 10. 成本估算

| 项 | 估算 |
| --- | --- |
| twitterapi.io | 每月几美元：每天 20 次 × 1～2 个请求，加上返回的原创推文，以及服务端过滤不掉的回复 |
| Kimi 翻译 | 只处理新内容，按每天约 100～200 条推文估算，量级很小（以实际 token 统计为准） |
| OSS 和 CDN | 图片按内容去重，量级很小 |
