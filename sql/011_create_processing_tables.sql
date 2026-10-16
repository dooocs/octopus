-- Processing layer tables (docs/feed-pipeline-design.md §5–6), Supabase/Postgres.
--
-- octp_pipelines         one row per configured pipeline (kind = code, input = config)
-- octp_pipeline_sources  hard dependencies on scraper configs: deleting a
--                        referenced config is refused by the database
-- octp_pipeline_runs     one row per run, with counts and LLM usage
-- proc_feed_posts        动态: one row per post (a tweet or a merged thread)
-- proc_feed_authors      动态: one row per author (card order + 24h summary)
--
-- RLS: the Octopus admin reads and edits pipelines; processed outputs are
-- written by the service role and read by the site build (secret key). No
-- anonymous access.

alter table public.octp_scraper_configs
add column if not exists archived_at timestamptz;

create table if not exists public.octp_pipelines (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  kind text not null,
  enabled boolean not null default true,
  input jsonb not null default '{}'::jsonb,
  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create table if not exists public.octp_pipeline_sources (
  pipeline_id uuid not null references public.octp_pipelines(id) on delete cascade,
  scraper_config_id uuid not null references public.octp_scraper_configs(id) on delete restrict,
  created_date timestamptz not null default now(),
  primary key (pipeline_id, scraper_config_id)
);

create table if not exists public.octp_pipeline_runs (
  id uuid primary key default gen_random_uuid(),
  pipeline_id uuid references public.octp_pipelines(id) on delete set null,
  status text not null default 'running'
    check (status in ('running', 'success', 'partial', 'failed')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  summary jsonb not null default '{}'::jsonb,
  error_message text
);

create table if not exists public.proc_feed_posts (
  id text primary key,
  raw_item_ids text[] not null,
  author text not null,
  published_at timestamptz not null,
  updated_at timestamptz not null,
  url text not null,
  text_original text not null,
  text_zh text not null,
  media jsonb not null default '[]'::jsonb,
  content_hash text not null,
  prompt_version int not null,
  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create table if not exists public.proc_feed_authors (
  author text primary key,
  display_name text not null,
  avatar_url text,
  digest_zh text,
  digest_input_hash text,
  latest_at timestamptz,
  digest_window_end timestamptz,
  post_count int not null default 0,
  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create index if not exists octp_pipeline_runs_pipeline_started_idx
on public.octp_pipeline_runs (pipeline_id, started_at desc);
create index if not exists proc_feed_posts_updated_at_idx
on public.proc_feed_posts (updated_at desc);
create index if not exists proc_feed_authors_latest_at_idx
on public.proc_feed_authors (latest_at desc);

drop trigger if exists octp_pipelines_set_updated_date on public.octp_pipelines;
create trigger octp_pipelines_set_updated_date
before update on public.octp_pipelines
for each row execute function public.octp_set_updated_date();

drop trigger if exists proc_feed_posts_set_updated_date on public.proc_feed_posts;
create trigger proc_feed_posts_set_updated_date
before update on public.proc_feed_posts
for each row execute function public.octp_set_updated_date();

drop trigger if exists proc_feed_authors_set_updated_date on public.proc_feed_authors;
create trigger proc_feed_authors_set_updated_date
before update on public.proc_feed_authors
for each row execute function public.octp_set_updated_date();

alter table public.octp_pipelines enable row level security;
alter table public.octp_pipeline_sources enable row level security;
alter table public.octp_pipeline_runs enable row level security;
alter table public.proc_feed_posts enable row level security;
alter table public.proc_feed_authors enable row level security;

grant select, insert, update, delete on public.octp_pipelines to authenticated;
grant select, insert, update, delete on public.octp_pipeline_sources to authenticated;
grant select on public.octp_pipeline_runs to authenticated;
grant select on public.proc_feed_posts to authenticated;
grant select on public.proc_feed_authors to authenticated;

do $$
declare
  admin_uid constant uuid := 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a';
  t text;
begin
  foreach t in array array['octp_pipelines', 'octp_pipeline_sources'] loop
    execute format('drop policy if exists "octopus admin can read %1$s" on public.%1$I', t);
    execute format('drop policy if exists "octopus admin can insert %1$s" on public.%1$I', t);
    execute format('drop policy if exists "octopus admin can update %1$s" on public.%1$I', t);
    execute format('drop policy if exists "octopus admin can delete %1$s" on public.%1$I', t);
    execute format('create policy "octopus admin can read %1$s" on public.%1$I for select to authenticated using (auth.uid() = %2$L::uuid)', t, admin_uid);
    execute format('create policy "octopus admin can insert %1$s" on public.%1$I for insert to authenticated with check (auth.uid() = %2$L::uuid)', t, admin_uid);
    execute format('create policy "octopus admin can update %1$s" on public.%1$I for update to authenticated using (auth.uid() = %2$L::uuid) with check (auth.uid() = %2$L::uuid)', t, admin_uid);
    execute format('create policy "octopus admin can delete %1$s" on public.%1$I for delete to authenticated using (auth.uid() = %2$L::uuid)', t, admin_uid);
  end loop;
  foreach t in array array['octp_pipeline_runs', 'proc_feed_posts', 'proc_feed_authors'] loop
    execute format('drop policy if exists "octopus admin can read %1$s" on public.%1$I', t);
    execute format('create policy "octopus admin can read %1$s" on public.%1$I for select to authenticated using (auth.uid() = %2$L::uuid)', t, admin_uid);
  end loop;
end $$;

-- The 动态 pipeline, disabled until the collection is verified on live data.
insert into public.octp_pipelines (slug, name, kind, enabled, input)
values (
  'feed_digest',
  '动态 · 翻译与博主摘要',
  'feed_digest',
  false,
  jsonb_build_object(
    'selector', jsonb_build_object('sub_source_types', jsonb_build_array('x_feed_accounts'), 'window_hours', 24),
    'llm', jsonb_build_object(
      'model', 'kimi-k2.6',
      'base_url', 'https://api.moonshot.cn/v1',
      'extra_body', jsonb_build_object('thinking', jsonb_build_object('type', 'disabled'))
    ),
    'prompt_version', 1
  )
)
on conflict (slug) do nothing;

insert into public.octp_pipeline_sources (pipeline_id, scraper_config_id)
select p.id, c.id
from public.octp_pipelines p
join public.octp_scraper_configs c on c.sub_source_type = 'x_feed_accounts'
where p.slug = 'feed_digest'
on conflict do nothing;
