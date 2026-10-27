-- ahaIndexSync's raw_items.scraper_slug is NOT NULL and references
-- scraper_configs(slug). Rows that octopus hands off (processing/aha_daily_handoff.py)
-- all point at this one marker config. It is disabled, and ahaIndexSync only loads
-- enabled configs, so it is never run; it only satisfies the foreign key.
-- The octopus source of each row is in raw_items.extra->'octopus'.
--
-- Idempotent.

insert into public.scraper_configs (scraper_type, name, enabled, priority, config, slug, source_type, content_type)
values (
  'octopus', 'Octopus（由 octopus 抓取后交接，勿启用）', false, 999,
  '{"note": "Marker for rows handed off by octopus aha_daily_handoff; never run by ahaIndexSync."}',
  'octopus', 'MIXED', 'mixed'
)
on conflict (slug) do nothing;
