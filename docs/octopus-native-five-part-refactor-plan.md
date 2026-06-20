# Octopus Native Five-Part Input Refactor Plan

本文档描述下一轮强迁移方案：把当前“五段式外层契约 + legacy flat engine 内核”彻底改造成“原生五段式 adapter pipeline”。

## 1. 当前状态

已经完成：

- 线上 `octp_scraper_configs.input` 已迁移为五段式：
  - `source`
  - `fetch`
  - `filters`
  - `enrich`
  - `runtime`
- Web admin 已从 `web/src/generated/scraper_specs.json` 渲染渠道和默认 input。
- `core.runner` 已按 `ChannelSpec.input_schema` 校验五段式 input。
- `Global Scrape` 已在 `main` 跑通真实爬取和 RDS 写入。

仍未完成：

- `sources/*` 仍继承 `LegacyEngineAdapter`。
- `LegacyEngineAdapter` 仍通过 `flat_config(config)` 把五段式 input 拍平成旧 dict。
- `scrapers/*Engine.fetch()` 仍直接读取 `self.config.get("min_score")`、`self.config.get("url")` 等 flat 字段。
- `core.contracts.ScraperConfig.from_mapping()` 仍接受旧 `config` 别名。
- `RawItem` 仍兼容 `content_type`。
- `BaseScraper` 仍存在于 `core.contracts` / `infra.models`。

因此当前不是原生五段式，只是运行入口和线上配置已五段式。

## 2. 最终目标

运行路径必须变成：

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

渠道代码必须直接读取：

```python
config.input.source
config.input.fetch
config.input.filters
config.input.enrich
config.input.runtime
```

最终代码中不再存在运行时依赖：

- `LegacyEngineAdapter`
- `flat_config`
- `BaseScraper`
- `scrapers/*Engine` 作为 runner 入口
- `RawItem(content_type=...)`
- `ScraperConfig.from_mapping(... row.get("config"))`
- `runtime_config_from_row()` 输出 `"config"`

`raw_items` 输出契约不变，仍由代码固定，不配置化。

## 3. 改造原则

- 配置只决定抓什么、抓多少、怎么过滤、做哪些二阶段补充。
- 输出字段、字段名、表结构、ID 规则不配置化。
- 渠道内部字段保留来源语义：
  - GitHub: `stars`
  - HackerNews: `score`
  - Product Hunt: `votes`
  - Reddit: `score`
  - Twitter/X: `likes`
- 不做跨渠道 `normalized_score`。
- adapter 不直接写 DB，不直接写 Supabase runtime tables。
- HTTP 请求继续走 `infra.http`。
- 每个渠道失败只影响自己的 task，不影响整轮 run。

## 4. Phase 1: 收紧 core 契约

目标：让 pipeline 结构先成为真实代码契约。

改动：

- `core.contracts.SourceAdapter`
  - 增加 `filter(ctx, records, config)`。
  - 保留 `discover/enrich/normalize`。
- `core.runner`
  - 调用 `adapter.filter()`，不再 `filtered = records`。
  - 明确统计：
    - `items_discovered`
    - `items_filtered`
    - `items_enriched`
  - task stage 覆盖：
    - `validate_config`
    - `discover`
    - `filter`
    - `enrich`
    - `normalize`
    - `validate_output`
    - `sink`
    - `done`
- `scripts.octp_supabase.runtime_config_from_row()`
  - 输出字段从 `config` 改成 `input`。
- `core.contracts.ScraperConfig.from_mapping()`
  - 只接受 `input`。
  - 删除 `row.get("config")` fallback。

验收：

```bash
rg -n 'row\.get\("config"|config\.get\("config"|"\s*config"\s*:' core scripts tests
python -m unittest discover tests
```

允许此阶段暂时保留 `LegacyEngineAdapter`，但它必须只作为未迁渠道临时桥接。

## 5. Phase 2: 抽公共工具

目标：把 `sources.legacy` 中仍有价值的 schema helper 拆出来，避免迁移每个渠道时复制样板。

新增：

```text
sources/schema.py
sources/common.py
```

`sources/schema.py`：

- `input_schema(...)`
- `default_input(...)`
- 常用 JSON Schema 常量：
  - `STRING`
  - `INTEGER`
  - `NUMBER`
  - `BOOLEAN`
  - `STRING_ARRAY`

`sources/common.py`：

- `has_enrich(config, name)`
- `runtime_timeout(config, default)`
- `runtime_retries(config, default)`
- `native_identity(record, *keys)`
- URL/date/text normalize helper

验收：

```bash
rg -n 'from \.legacy import .*input_schema|from \.legacy import .*default_input' sources
```

结果应为空；渠道 spec 只能从 `sources.schema` 引入 schema helper。

## 6. Phase 3: 低风险渠道原生化

优先迁移 HTML/RSS 类，风险低、反馈快。

### 6.1 RSS

配置读取：

- `source.url`
- `fetch.max_items`
- `fetch.fetch_window_hours`
- `runtime.timeout`

实现：

- `discover`: 请求 RSS，解析 feed entries，返回候选 `SourceRecord`。
- `filter`: 按 `fetch.fetch_window_hours` 过滤发布时间。
- `enrich`: no-op。
- `normalize`: 输出 `RawItem(item_type=config.item_type)`。

删除依赖：

- `scrapers.rss_feed.RSSFeedEngine`

### 6.2 AI Blog

配置读取：

- `source.base_url`
- `source.news_url`
- `source.link_selector`
- `source.author`
- `source.source_tag`
- `fetch.fetch_window_hours`

实现：

- `discover`: 抓新闻页，按 selector 取链接。
- `filter`: 按窗口过滤。
- `enrich`: 可暂时 no-op，正文为页面摘要/标题上下文。
- `normalize`: `extra.source_tag` 保留来源标签。

删除依赖：

- `scrapers.ai_blog.AIBlogEngine`

### 6.3 V2EX

配置读取：

- `source.source_tag`
- `fetch.top_n`
- `fetch.max_replies_to_fetch`
- `fetch.max_replies_to_keep`
- `enrich.top_replies`

实现：

- `discover`: 拉热榜 topic。
- `filter`: no-op 或按 reply/click 阈值扩展。
- `enrich`: 如果启用 `top_replies`，补充回复。
- `normalize`: `metrics.replies/clicks` 保持原名。

删除依赖：

- `scrapers.community_v2ex.V2EXEngine`

### 6.4 LinuxDo

配置读取：

- `source.source_tag`
- `fetch.top_n`
- `fetch.max_replies_to_fetch`
- `enrich.top_replies`

实现同 V2EX。

删除依赖：

- `scrapers.community_linuxdo.LinuxDoEngine`

Phase 3 验收：

```bash
rg -n 'LegacyEngineAdapter|flat_config|RSSFeedEngine|AIBlogEngine|V2EXEngine|LinuxDoEngine' sources scrapers tests
python -m unittest discover tests
python -m compileall core infra pipeline sources scripts tests
```

## 7. Phase 4: API 类渠道原生化

### 7.1 HackerNews

配置读取：

- `source.feed`
- `fetch.new_n`
- `fetch.cutoff_hours`
- `fetch.fetch_workers`
- `fetch.skip_domains`
- `filters.min_score`
- `enrich.article_body`

实现：

- `discover`: 拉 story ids 和 story item。
- `filter`: `score >= min_score`，跳过域名，按 cutoff。
- `enrich`: 如果启用 `article_body`，抓外链正文。
- `normalize`: `metrics.score/comments/hn_id/hn_url`。

### 7.2 Reddit

配置读取：

- `source.subreddit`
- `fetch.max_retries`
- `filters.min_score`
- `filters.skip_nsfw`
- `filters.skip_stickied`
- `filters.skip_discussion_below`
- `filters.skip_self_text_below`

实现：

- `discover`: 拉 subreddit hot/new。
- `filter`: score/nsfw/stickied/text length。
- `enrich`: no-op。
- `normalize`: `metrics.score/comments`。

### 7.3 Product Hunt

配置读取：

- `source.api_token`
- `fetch.max_retries`
- `filters.min_votes`
- `filters.topic_whitelist`
- `filters.topic_blacklist`

实现：

- `discover`: GraphQL posts。
- `filter`: votes/topic allow/deny。
- `enrich`: makers/topics/media 结构化进 `context_content`。
- `normalize`: `metrics.votes/comments`。

### 7.4 Hugging Face Papers

配置读取：

- `fetch.top_n`
- `fetch.max_retries`

实现：

- `discover`: daily papers。
- `filter`: no-op。
- `enrich`: no-op。
- `normalize`: `metrics.upvotes/num_comments`。

### 7.5 Hugging Face Models

配置读取：

- `fetch.limit`
- `fetch.max_retries`
- `filters.min_likes`
- `filters.min_downloads`
- `filters.quant_suffixes`
- `filters.deriv_suffixes`

实现：

- `discover`: trending models。
- `filter`: likes/downloads/suffix rules。
- `enrich`: no-op。
- `normalize`: `metrics.likes/downloads`。

Phase 4 验收：

```bash
rg -n 'HackerNewsEngine|RedditEngine|ProductHuntEngine|HuggingFace.*Engine' sources scrapers tests
python -m unittest discover tests
```

## 8. Phase 5: 高复杂渠道原生化

### 8.1 GitHub Trending

配置读取：

- `fetch.timeout`
- `enrich.github_readme`
- `enrich.github_languages`
- `enrich.github_images`
- `enrich.star_history`

实现：

- `discover`: 解析 trending 榜单，生成 repo candidate。
- `filter`: no-op。
- `enrich`: README、languages、images、star history 分步骤执行。
- `normalize`: `metrics.stars/rank`，repo id 或 full name 作为 identity。

### 8.2 GitHub Search

配置读取：

- `source.queries`
- `fetch.per_page`
- `fetch.fetch_window_days`
- `fetch.max_readme_images`
- `fetch.badge_patterns`
- `filters.min_stars`
- `enrich.github_readme`
- `enrich.github_languages`
- `enrich.github_images`
- `enrich.star_history`

实现：

- `discover`: 按 query 搜 GitHub repos。
- `filter`: `stargazers_count >= min_stars` 和时间窗口。
- `enrich`: README/languages/images/star history。
- `normalize`: `metrics.stars/forks/watchers/open_issues`。

### 8.3 Twitter / X

配置读取：

- `source.watch_accounts`
- `source.tracked_keywords`
- `fetch.max_age_days`
- `filters.timeline_min_faves`
- `filters.min_likes`

实现：

- `discover`: 账号 timeline 和关键词 search。
- `filter`: 时间窗口、likes/faves。
- `enrich`: no-op。
- `normalize`: `metrics.likes/retweets/replies/views`。

Phase 5 验收：

```bash
rg -n 'GitHub.*Engine|TwitterTwscrapeEngine|LegacyEngineAdapter|flat_config' sources scrapers tests
python -m unittest discover tests
```

## 9. Phase 6: 删除 legacy 兼容层

全部渠道原生后删除：

- `sources/legacy.py`
- `core.contracts.BaseScraper`
- `infra.models.BaseScraper`
- `scrapers/*` 运行时 engine 文件，或移动到 archived/test fixture，确保 runner 不引用。
- `RawItem.content_type` 入参和 property。
- 所有测试中的 `content_type=`。

保留：

- `scrapers.registry` 可作为薄 wrapper，但只转发 `core.registry`，不暴露 engine。
- `scripts/migrate_scraper_configs_v1.py` 可保留为历史迁移工具，但不参与运行路径。

强校验：

```bash
rg -n 'LegacyEngineAdapter|flat_config|BaseScraper|content_type|row\.get\("config"|config\.get\("config"' core infra sources scrapers scripts tests
```

预期：

- 运行时代码无命中。
- 如果历史迁移脚本或文档保留命中，必须标注为 migration-only，不被 runner import。

## 10. 前端同步

每次渠道 spec 变化后执行：

```bash
python scripts/export_scraper_specs.py
cd web && npm run build
```

前端增强项：

- 保存前按 generated `input_schema` 校验内部字段。
- 禁止创建 generated specs 中不存在的 scraper。
- disabled 且 unsupported 的历史配置只允许查看或保持 disabled，不允许启用。

## 11. 测试矩阵

### 单元测试

- `InputConfig` 只接受五段式。
- `ScraperConfig` 只接受 `input`。
- `SourceAdapter` 必须实现 `filter`。
- `RawItem` 只接受 `item_type`。
- `core.runner` 正确执行 `discover -> filter -> enrich -> normalize`。

### 渠道 fixture 测试

每个 adapter 至少一组 fixture：

- `discover` 返回 `SourceRecord`。
- `filter` 过滤符合渠道语义。
- `enrich` 只补二阶段字段。
- `normalize` 输出符合 `RawItem` contract。
- `metrics` 保留原生字段名。
- `context_content` 和 `extra` 不混入运行态字段。

### 集成测试

```bash
python -m compileall core infra pipeline sources scripts tests
python -m unittest discover tests
python scripts/export_scraper_specs.py
cd web && npm run build
```

### 线上验收

```bash
gh workflow run "Global Scrape" --ref main -f snapshot_date= -f write_rds=true -f fail_on_error=false
```

验收标准：

- workflow `success`
- `configs > 0`
- `skipped = 0`
- `failed = 0`
- `written > 0`
- `octp_scrape_tasks.status` 全部为 `success`
- `octp_scrape_tasks.stage` 全部为 `done`
- `octp_snapshot_raw_items` 当日刷新成功

## 12. 推荐 PR 拆分

### PR 1: Core pipeline contract

- 增加 `filter` 阶段。
- `runtime_config_from_row` 改为输出 `input`。
- `ScraperConfig` 删除 `config` fallback。
- 保留 legacy adapter 临时桥接。

### PR 2: Low-risk native adapters

- RSS
- AI Blog
- V2EX
- LinuxDo

### PR 3: API native adapters

- HackerNews
- Reddit
- Product Hunt
- Hugging Face Papers
- Hugging Face Models

### PR 4: High-complexity native adapters

- GitHub Trending
- GitHub Search
- Twitter / X

### PR 5: Remove legacy runtime

- 删除 `LegacyEngineAdapter`
- 删除 `flat_config`
- 删除 `BaseScraper`
- 删除 `content_type` 兼容
- 更新测试和文档

### PR 6: Production verification

- 重新导出 specs。
- Web build。
- 触发真实 `Global Scrape`。
- 根据线上结果修 provider 边界问题。

## 13. 风险点

- GitHub API rate limit 可能导致 fixture 通过但线上少量失败，需要 fixture 和 live run 分开看。
- Twitter/X 第三方 API 返回结构不稳定，需要 normalize 边界更严格。
- Product Hunt token 为空时要清晰失败，不能静默返回误导性成功。
- RSS 时间字段不统一，filter 需要允许无发布时间 fallback。
- 删除 `content_type` 前必须确认 RDS DAO、snapshot sync、前端都只读 `item_type`。
- 删除 `config` fallback 前必须确认线上所有 enabled config 已是五段式 `input`。

## 14. 完成定义

只有同时满足以下条件，才算“全部原生五段式”完成：

- 所有 enabled scraper adapter 直接读取 `config.input.*`。
- runner 不再接受旧 `config` 字段。
- 运行时代码不存在 `LegacyEngineAdapter` / `flat_config` / `BaseScraper`。
- `RawItem` 不再接受 `content_type`。
- 所有渠道 fixture 测试通过。
- `Global Scrape` 在 `main` 上真实跑通，并写入 RDS。
- Web admin 只能创建 generated specs 中存在的 scraper。
- `raw_items` 输出契约保持不变。
