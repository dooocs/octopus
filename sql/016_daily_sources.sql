-- Daily sources for the AI 日报: 新产品 (Product Hunt, Show HN, DevHunt, Uneed,
-- MicroLaunch), 独立开发 (TrustMRR) and Reddit r/LocalLLaMA's top post of the
-- day. Octopus scrapes them (.github/workflows/daily_sources.yml, 00:40
-- Beijing); the aha_daily_handoff pipeline copies the day's rows into
-- ahaIndexSync's raw_items + items_content before its 02:00 run.
--
-- Part 2 changes ahaIndexSync's config tables (same Supabase project): the rank
-- groups that decide which sources reach the homepage, display metrics for the
-- new content types, and disabling ahaIndexSync's own Product Hunt and
-- r/LocalLLaMA scrapers (they never produced a row) so nothing is fetched twice.
--
-- Idempotent: safe to run more than once.

begin;

-- ── Part 1: octopus ────────────────────────────────────────────────────────

insert into public.octp_item_types (item_type, name, description) values
  ('startup', 'Startup', 'A company with verified metrics, e.g. Stripe-verified MRR')
on conflict (item_type) do nothing;

insert into public.octp_scraper_configs (
  name, scraper, enabled, priority, source_type, sub_source_type, item_type, input_schema_version, input
) values
  ('Show HN', 'show_hn', true, 40, 'PRODUCT', 'show_hn', 'product', 1,
   '{"source":{},"fetch":{"window_hours":24,"hits_per_page":100,"limit":5},"filters":{"min_points":20},"enrich":[]}'),
  ('DevHunt', 'devhunt', true, 66, 'PRODUCT', 'devhunt', 'product', 1,
   '{"source":{},"fetch":{"limit":1,"repeat_ttl_days":7},"filters":{"min_votes":5},"enrich":[]}'),
  ('Uneed', 'uneed', true, 67, 'PRODUCT', 'uneed', 'product', 1,
   '{"source":{},"fetch":{"day_offset":1,"limit":2},"filters":{"min_votes":0},"enrich":[{"name":"product_details","when":"always"}]}'),
  ('MicroLaunch', 'microlaunch', true, 68, 'PRODUCT', 'microlaunch', 'product', 1,
   '{"source":{},"fetch":{"limit":1,"repeat_ttl_days":30},"filters":{"min_score_week":5},"enrich":[]}'),
  ('TrustMRR', 'trustmrr', true, 70, 'STARTUP', 'trustmrr', 'startup', 1,
   '{"source":{},"fetch":{"limit":1,"repeat_ttl_days":30},"filters":{"min_mrr":5000,"min_prior_mrr":1000,"skip_stealth":true},"enrich":[]}')
on conflict (sub_source_type) do nothing;

-- Product Hunt: keep the 5 most-voted that pass the existing filters.
update public.octp_scraper_configs
set input = jsonb_set(input, '{fetch,limit}', '5'), updated_date = now()
where sub_source_type = 'product_hunt' and archived_at is null;

-- r/LocalLLaMA: the single hottest post of the last 24 hours. Score filters off,
-- since the RSS fallback (GitHub runner IPs get 403 on JSON) carries no score.
update public.octp_scraper_configs
set input = '{"source":{"subreddit":"LocalLLaMA"},"fetch":{"max_retries":3,"post_limit":10,"keep_top":1,"max_comments_to_keep":10},"filters":{"min_score":0,"skip_nsfw":true,"skip_stickied":true,"skip_discussion_below":0,"skip_self_text_below":0},"enrich":[{"name":"top_comments","when":"always"}]}',
    updated_date = now()
where sub_source_type = 'reddit_r_localllama' and archived_at is null;

insert into public.octp_pipelines (slug, name, kind, enabled, input)
select 'aha_daily_handoff', 'AI 日报 · 交给 ahaIndexSync', 'aha_daily_handoff', true,
  '{"timezone":"Asia/Shanghai",
    "target":{"raw_table":"raw_items","content_table":"items_content"},
    "sources":{
      "product_hunt":{"source_name":"Product Hunt","source_type":"PRODUCT","content_type":"product_hunt"},
      "show_hn":{"source_name":"Show HN","source_type":"PRODUCT","content_type":"news"},
      "devhunt":{"source_name":"DevHunt","source_type":"PRODUCT","content_type":"product"},
      "uneed":{"source_name":"Uneed","source_type":"PRODUCT","content_type":"product"},
      "microlaunch":{"source_name":"MicroLaunch","source_type":"PRODUCT","content_type":"product"},
      "trustmrr":{"source_name":"TrustMRR","source_type":"STARTUP","content_type":"startup"},
      "reddit_r_localllama":{"source_name":"Reddit r/LocalLLaMA","source_type":"NEWS","content_type":"reddit"}}}'::jsonb
where not exists (select 1 from public.octp_pipelines where slug = 'aha_daily_handoff');

-- ── Part 2: ahaIndexSync config ────────────────────────────────────────────

insert into public.display_metrics_configs (content_type, metrics)
select v.content_type, v.metrics::jsonb
from (values
  ('product', '[{"key":"votes","label":"▲ 投票","format":"number"}]'),
  ('startup', '[{"key":"mrr_display","label":"💰 MRR","format":"text"},{"key":"growth_display","label":"📈 净增","format":"text"}]')
) as v(content_type, metrics)
where not exists (select 1 from public.display_metrics_configs d where d.content_type = v.content_type);

-- "AI 产品" (Product Hunt only, which never produced a row) becomes 新产品.
update public.rank_group_configs
set group_name = '新产品',
    source_names = array['Product Hunt', 'Show HN', 'DevHunt', 'Uneed', 'MicroLaunch'],
    "limit" = 4,
    updated_at = now()
where group_name in ('AI 产品', '新产品');

-- r/LocalLLaMA gets its own slot instead of competing with HN for 技术社区.
update public.rank_group_configs
set source_names = array_remove(source_names, 'Reddit r/LocalLLaMA'), updated_at = now()
where group_name = '技术社区';

insert into public.rank_group_configs (group_name, source_names, "limit", must_include, sort_order, enabled)
select v.group_name, v.source_names, v.lim, false, v.sort_order, true
from (values
  ('独立开发', array['TrustMRR'], 1, 26),
  ('Reddit 热帖', array['Reddit r/LocalLLaMA'], 1, 35)
) as v(group_name, source_names, lim, sort_order)
where not exists (select 1 from public.rank_group_configs g where g.group_name = v.group_name);

update public.scraper_configs
set enabled = false, updated_at = now()
where slug in ('product-hunt', 'reddit-r-localllama');

commit;
