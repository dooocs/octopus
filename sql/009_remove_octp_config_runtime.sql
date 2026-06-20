-- Remove the legacy execution/runtime input section before four-part
-- scraper config validation is deployed.

update public.octp_scraper_configs
set input = input - 'runtime'
where input ? 'runtime';
