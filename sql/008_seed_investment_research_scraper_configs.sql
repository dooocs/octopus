-- Seed investment-research channels for AI, compute, optical modules,
-- semiconductors, and major bank research signals.
--
-- Stable company filings are enabled by default through SEC Atom feeds.
-- Web page candidates that need selector tuning are inserted disabled.

insert into public.octp_item_types (item_type, name, description) values
  ('filing', 'Filing', 'Regulatory filings and exchange disclosure documents'),
  ('transcript', 'Transcript', 'Earnings call and event transcript records'),
  ('report', 'Report', 'Research reports, public insight notes, and white papers')
on conflict (item_type) do update
set
  name = excluded.name,
  description = excluded.description;

with company_filings (
  ticker,
  sec_cik_param,
  company_name,
  company_slug,
  coverage_group,
  theme_tags,
  priority
) as (
  values
    ('NVDA', 'NVDA', 'NVIDIA', 'nvidia', 'ai_compute', array['ai', 'ai_compute', 'gpu', 'semiconductor']::text[], 300),
    ('AMD', 'AMD', 'Advanced Micro Devices', 'amd', 'ai_compute', array['ai', 'ai_compute', 'gpu', 'semiconductor']::text[], 301),
    ('AVGO', 'AVGO', 'Broadcom', 'broadcom', 'ai_compute', array['ai_compute', 'networking', 'custom_silicon', 'semiconductor']::text[], 302),
    ('MRVL', 'MRVL', 'Marvell Technology', 'marvell', 'ai_compute', array['ai_compute', 'networking', 'optical_connectivity', 'semiconductor']::text[], 303),
    ('SMCI', 'SMCI', 'Super Micro Computer', 'supermicro', 'ai_compute', array['ai_compute', 'server', 'data_center']::text[], 304),
    ('ANET', 'ANET', 'Arista Networks', 'arista', 'ai_compute', array['ai_compute', 'networking', 'data_center']::text[], 305),
    ('MSFT', 'MSFT', 'Microsoft', 'microsoft', 'ai_platform', array['ai', 'cloud', 'ai_platform', 'hyperscaler']::text[], 306),
    ('GOOGL', 'GOOGL', 'Alphabet', 'alphabet', 'ai_platform', array['ai', 'cloud', 'ai_platform', 'hyperscaler']::text[], 307),
    ('AMZN', 'AMZN', 'Amazon', 'amazon', 'ai_platform', array['ai', 'cloud', 'ai_platform', 'hyperscaler']::text[], 308),
    ('META', 'META', 'Meta Platforms', 'meta', 'ai_platform', array['ai', 'ai_platform', 'hyperscaler']::text[], 309),
    ('ORCL', 'ORCL', 'Oracle', 'oracle', 'ai_compute', array['ai_compute', 'cloud', 'database']::text[], 310),
    ('ARM', '0001973239', 'Arm Holdings', 'arm', 'semiconductor', array['semiconductor', 'ip', 'cpu']::text[], 311),
    ('INTC', 'INTC', 'Intel', 'intel', 'semiconductor', array['semiconductor', 'cpu', 'foundry']::text[], 312),
    ('QCOM', 'QCOM', 'Qualcomm', 'qualcomm', 'semiconductor', array['semiconductor', 'mobile_ai', 'edge_ai']::text[], 313),
    ('TSM', 'TSM', 'TSMC', 'tsmc', 'semiconductor', array['semiconductor', 'foundry']::text[], 314),
    ('ASML', 'ASML', 'ASML', 'asml', 'semicap', array['semiconductor', 'semicap', 'lithography']::text[], 315),
    ('AMAT', 'AMAT', 'Applied Materials', 'applied_materials', 'semicap', array['semiconductor', 'semicap', 'equipment']::text[], 316),
    ('LRCX', 'LRCX', 'Lam Research', 'lam_research', 'semicap', array['semiconductor', 'semicap', 'equipment']::text[], 317),
    ('KLAC', 'KLAC', 'KLA', 'kla', 'semicap', array['semiconductor', 'semicap', 'inspection']::text[], 318),
    ('MU', 'MU', 'Micron Technology', 'micron', 'memory', array['semiconductor', 'memory', 'hbm']::text[], 319),
    ('COHR', 'COHR', 'Coherent', 'coherent', 'optical_module', array['optical_module', 'optical_components', 'datacenter_interconnect']::text[], 320),
    ('LITE', 'LITE', 'Lumentum', 'lumentum', 'optical_module', array['optical_module', 'optical_components', 'datacenter_interconnect']::text[], 321),
    ('AAOI', 'AAOI', 'Applied Optoelectronics', 'applied_optoelectronics', 'optical_module', array['optical_module', 'datacenter_interconnect']::text[], 322),
    ('FN', 'FN', 'Fabrinet', 'fabrinet', 'optical_module', array['optical_module', 'manufacturing', 'datacenter_interconnect']::text[], 323),
    ('CIEN', 'CIEN', 'Ciena', 'ciena', 'optical_module', array['optical_module', 'networking', 'datacenter_interconnect']::text[], 324),
    ('GS', 'GS', 'Goldman Sachs', 'goldman_sachs', 'broker_dealer', array['broker', 'market_research', 'financials']::text[], 325),
    ('MS', 'MS', 'Morgan Stanley', 'morgan_stanley', 'broker_dealer', array['broker', 'market_research', 'financials']::text[], 326),
    ('JPM', 'JPM', 'JPMorgan Chase', 'jpmorgan_chase', 'broker_dealer', array['broker', 'market_research', 'financials']::text[], 327)
),
filing_configs as (
  select
    company_name || ' SEC Filings' as name,
    'rss' as scraper,
    true as enabled,
    priority,
    'regulatory' as source_type,
    'sec_edgar_' || lower(ticker) || '_filings' as sub_source_type,
    'filing' as item_type,
    1 as input_schema_version,
    jsonb_build_object(
      'source', jsonb_build_object(
        'url', 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=' || sec_cik_param || '&type=&dateb=&owner=exclude&count=40&output=atom',
        'source_tag', 'sec_edgar',
        'metadata', jsonb_build_object(
          'company_ticker', ticker,
          'company_name', company_name,
          'company_slug', company_slug,
          'coverage_group', coverage_group,
          'theme_tags', to_jsonb(theme_tags),
          'source_role', 'company_filings',
          'authority', 'sec_edgar'
        )
      ),
      'fetch', jsonb_build_object(
        'max_items', 20,
        'fetch_window_hours', 720
      ),
      'filters', '{}'::jsonb,
      'enrich', '[]'::jsonb
    ) as input
  from company_filings
)
insert into public.octp_scraper_configs (
  name,
  scraper,
  enabled,
  priority,
  source_type,
  sub_source_type,
  item_type,
  input_schema_version,
  input
)
select
  name,
  scraper,
  enabled,
  priority,
  source_type,
  sub_source_type,
  item_type,
  input_schema_version,
  input
from filing_configs
on conflict (sub_source_type) do update
set
  name = excluded.name,
  scraper = excluded.scraper,
  enabled = excluded.enabled,
  priority = excluded.priority,
  source_type = excluded.source_type,
  item_type = excluded.item_type,
  input_schema_version = excluded.input_schema_version,
  input = excluded.input;

with official_feed_configs (
  name,
  priority,
  sub_source_type,
  item_type,
  url,
  source_tag,
  company_ticker,
  company_name,
  company_slug,
  coverage_group,
  source_role,
  theme_tags
) as (
  values
    ('NVIDIA Official Blog', 250, 'company_nvda_official_blog', 'article', 'https://blogs.nvidia.com/feed/', 'nvidia_blog', 'NVDA', 'NVIDIA', 'nvidia', 'ai_compute', 'official_blog', array['ai', 'ai_compute', 'gpu', 'semiconductor']::text[]),
    ('AMD IR News', 251, 'company_amd_ir_news', 'article', 'https://ir.amd.com/news-events/press-releases/rss', 'amd_ir', 'AMD', 'Advanced Micro Devices', 'amd', 'ai_compute', 'ir_news', array['ai', 'ai_compute', 'gpu', 'semiconductor']::text[]),
    ('Intel IR News', 252, 'company_intc_ir_news', 'article', 'https://www.intc.com/news-events/press-releases/rss', 'intel_ir', 'INTC', 'Intel', 'intel', 'semiconductor', 'ir_news', array['semiconductor', 'cpu', 'foundry']::text[]),
    ('Microsoft Official Blog', 253, 'company_msft_official_blog', 'article', 'https://blogs.microsoft.com/feed/', 'microsoft_blog', 'MSFT', 'Microsoft', 'microsoft', 'ai_platform', 'official_blog', array['ai', 'cloud', 'ai_platform', 'hyperscaler']::text[]),
    ('AWS Machine Learning Blog', 254, 'company_amzn_aws_ml_blog', 'article', 'https://aws.amazon.com/blogs/machine-learning/feed/', 'aws_ml_blog', 'AMZN', 'Amazon', 'amazon', 'ai_platform', 'official_blog', array['ai', 'cloud', 'ml_platform', 'hyperscaler']::text[]),
    ('Google AI Blog', 255, 'company_googl_google_ai_blog', 'article', 'https://blog.google/technology/ai/rss/', 'google_ai_blog', 'GOOGL', 'Alphabet', 'alphabet', 'ai_platform', 'official_blog', array['ai', 'ai_platform', 'hyperscaler']::text[])
),
official_configs as (
  select
    name,
    'rss' as scraper,
    true as enabled,
    priority,
    'website' as source_type,
    sub_source_type,
    item_type,
    1 as input_schema_version,
    jsonb_build_object(
      'source', jsonb_build_object(
        'url', url,
        'source_tag', source_tag,
        'metadata', jsonb_build_object(
          'company_ticker', company_ticker,
          'company_name', company_name,
          'company_slug', company_slug,
          'coverage_group', coverage_group,
          'theme_tags', to_jsonb(theme_tags),
          'source_role', source_role,
          'authority', 'company_official'
        )
      ),
      'fetch', jsonb_build_object(
        'max_items', 20,
        'fetch_window_hours', 168
      ),
      'filters', '{}'::jsonb,
      'enrich', '[]'::jsonb
    ) as input
  from official_feed_configs
)
insert into public.octp_scraper_configs (
  name,
  scraper,
  enabled,
  priority,
  source_type,
  sub_source_type,
  item_type,
  input_schema_version,
  input
)
select
  name,
  scraper,
  enabled,
  priority,
  source_type,
  sub_source_type,
  item_type,
  input_schema_version,
  input
from official_configs
on conflict (sub_source_type) do update
set
  name = excluded.name,
  scraper = excluded.scraper,
  enabled = excluded.enabled,
  priority = excluded.priority,
  source_type = excluded.source_type,
  item_type = excluded.item_type,
  input_schema_version = excluded.input_schema_version,
  input = excluded.input;

with broker_insight_configs (
  name,
  priority,
  sub_source_type,
  base_url,
  news_url,
  link_selector,
  institution_slug,
  institution_name
) as (
  values
    ('Goldman Sachs Public Insights', 450, 'broker_goldman_sachs_public_insights', 'https://www.goldmansachs.com', 'https://www.goldmansachs.com/insights', 'a[href*=''/insights/'']', 'goldman_sachs', 'Goldman Sachs'),
    ('Morgan Stanley Public Insights', 451, 'broker_morgan_stanley_public_insights', 'https://www.morganstanley.com', 'https://www.morganstanley.com/insights', 'a[href*=''/insights/''], a[href*=''/ideas/'']', 'morgan_stanley', 'Morgan Stanley'),
    ('JPMorgan Public Insights', 452, 'broker_jpmorgan_public_insights', 'https://www.jpmorgan.com', 'https://www.jpmorgan.com/insights', 'a[href*=''/insights/'']', 'jpmorgan', 'JPMorgan')
),
broker_configs as (
  select
    name,
    'ai_blog' as scraper,
    false as enabled,
    priority,
    'website' as source_type,
    sub_source_type,
    'report' as item_type,
    1 as input_schema_version,
    jsonb_build_object(
      'source', jsonb_build_object(
        'base_url', base_url,
        'news_url', news_url,
        'link_selector', link_selector,
        'author', institution_name,
        'source_tag', institution_slug || '_public_insights',
        'metadata', jsonb_build_object(
          'institution_slug', institution_slug,
          'institution_name', institution_name,
          'coverage_group', 'broker_research',
          'theme_tags', to_jsonb(array['broker', 'market_research', 'macro', 'ai']::text[]),
          'source_role', 'broker_public_insights',
          'authority', 'institution_public_site'
        )
      ),
      'fetch', jsonb_build_object(
        'fetch_window_hours', 0
      ),
      'filters', '{}'::jsonb,
      'enrich', '[]'::jsonb
    ) as input
  from broker_insight_configs
)
insert into public.octp_scraper_configs (
  name,
  scraper,
  enabled,
  priority,
  source_type,
  sub_source_type,
  item_type,
  input_schema_version,
  input
)
select
  name,
  scraper,
  enabled,
  priority,
  source_type,
  sub_source_type,
  item_type,
  input_schema_version,
  input
from broker_configs
on conflict (sub_source_type) do update
set
  name = excluded.name,
  scraper = excluded.scraper,
  enabled = excluded.enabled,
  priority = excluded.priority,
  source_type = excluded.source_type,
  item_type = excluded.item_type,
  input_schema_version = excluded.input_schema_version,
  input = excluded.input;
