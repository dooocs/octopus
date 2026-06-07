-- Copy the legacy public.scraper_configs rows into the new Octopus config table.
-- The legacy row id is preserved so existing references remain traceable.

insert into public.octp_scraper_configs (
  id,
  name,
  scraper,
  enabled,
  priority,
  source_type,
  sub_source_type,
  item_type,
  input,
  created_date,
  updated_date
)
select
  id,
  name,
  scraper_type as scraper,
  enabled,
  priority,
  source_type,
  trim(both '_' from regexp_replace(lower(slug), '[^a-z0-9]+', '_', 'g')) as sub_source_type,
  case
    when content_type = 'repo' then 'repo'
    when content_type = 'article' then 'article'
    when content_type = 'tweet' then 'post'
    when content_type in ('reddit', 'v2ex_hot', 'linuxdo_hot') then 'discussion'
    when content_type = 'hf_model' then 'model'
    when content_type = 'hf_papers' then 'paper'
    when content_type = 'product_hunt' then 'product'
    else content_type
  end as item_type,
  config - 'source_type' - 'content_type' - 'source_tag' as input,
  created_at as created_date,
  updated_at as updated_date
from public.scraper_configs
on conflict (id) do update
set
  name = excluded.name,
  scraper = excluded.scraper,
  enabled = excluded.enabled,
  priority = excluded.priority,
  source_type = excluded.source_type,
  sub_source_type = excluded.sub_source_type,
  item_type = excluded.item_type,
  input = excluded.input,
  updated_date = excluded.updated_date;
