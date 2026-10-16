-- 动态 tab: a separate scraper config for the hourly X feed
-- (docs/feed-pipeline-design.md §4). Its watch list starts as a copy of the
-- daily "X AI Leaders" config, so the two lists can change independently.
-- Created disabled: enable it once twitterapi.io has credits and a manual run
-- has been checked against live data.

insert into public.octp_scraper_configs (
  name, scraper, enabled, priority,
  source_type, sub_source_type, item_type,
  input_schema_version, input
)
select
  '动态 · X 博主', 'twitter_feed', false, 100,
  'social', 'x_feed_accounts', 'post',
  1,
  jsonb_build_object(
    'source', jsonb_build_object('watch_accounts', coalesce(leaders.input -> 'source' -> 'watch_accounts', '[]'::jsonb)),
    'fetch', jsonb_build_object('max_lookback_hours', 24, 'overlap_minutes', 5, 'max_pages', 25),
    'filters', '{}'::jsonb,
    'enrich', jsonb_build_array(jsonb_build_object('name', 'media_archive'))
  )
from (
  select input from public.octp_scraper_configs
  where scraper = 'twitter_twscrape' and name = 'X AI Leaders'
  order by updated_date desc
  limit 1
) as leaders
on conflict (sub_source_type) do nothing;
