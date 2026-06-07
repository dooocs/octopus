-- Octopus scraper admin tables for Supabase/Postgres.
-- These tables store scraper input configuration, item type dictionary values,
-- and one execution log row per scraper run.

create extension if not exists pgcrypto with schema extensions;

create or replace function public.octp_set_updated_date()
returns trigger
language plpgsql
as $$
begin
  new.updated_date = now();
  return new;
end;
$$;

create table if not exists public.octp_item_types (
  item_type text primary key,
  name text not null,
  description text,

  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create table if not exists public.octp_scraper_configs (
  id uuid primary key default gen_random_uuid(),

  name text not null,
  scraper text not null,

  enabled boolean not null default true,
  priority int not null default 100,

  source_type text not null,
  sub_source_type text not null,
  item_type text not null references public.octp_item_types(item_type),

  input jsonb not null default '{}'::jsonb,

  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create table if not exists public.octp_scraper_logs (
  id uuid primary key default gen_random_uuid(),

  scraper_config_id uuid references public.octp_scraper_configs(id) on delete set null,
  snapshot_date date not null,

  github_run_id text,

  config_snapshot jsonb not null default '{}'::jsonb,

  status text not null default 'running'
    check (status in ('running', 'success', 'failed', 'partial', 'skipped')),

  duration_ms int,
  result jsonb not null default '{}'::jsonb,

  error_message text,
  error_logs jsonb not null default '[]'::jsonb,

  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create unique index if not exists octp_scraper_configs_sub_source_type_key
on public.octp_scraper_configs (sub_source_type);

create index if not exists octp_scraper_configs_enabled_priority_idx
on public.octp_scraper_configs (enabled, priority, scraper);

create index if not exists octp_scraper_logs_config_date_idx
on public.octp_scraper_logs (scraper_config_id, snapshot_date desc);

create index if not exists octp_scraper_logs_github_run_id_idx
on public.octp_scraper_logs (github_run_id);

create index if not exists octp_scraper_logs_status_date_idx
on public.octp_scraper_logs (status, created_date desc);

drop trigger if exists octp_item_types_set_updated_date on public.octp_item_types;
create trigger octp_item_types_set_updated_date
before update on public.octp_item_types
for each row execute function public.octp_set_updated_date();

drop trigger if exists octp_scraper_configs_set_updated_date on public.octp_scraper_configs;
create trigger octp_scraper_configs_set_updated_date
before update on public.octp_scraper_configs
for each row execute function public.octp_set_updated_date();

drop trigger if exists octp_scraper_logs_set_updated_date on public.octp_scraper_logs;
create trigger octp_scraper_logs_set_updated_date
before update on public.octp_scraper_logs
for each row execute function public.octp_set_updated_date();

insert into public.octp_item_types (item_type, name, description) values
  ('article', 'Article', 'Articles, blogs, and news items'),
  ('repo', 'Repository', 'Code repositories'),
  ('product', 'Product', 'Products and launches'),
  ('model', 'Model', 'AI or machine learning models'),
  ('paper', 'Paper', 'Research papers'),
  ('post', 'Post', 'Social network posts'),
  ('discussion', 'Discussion', 'Community discussion topics and threads')
on conflict (item_type) do update
set
  name = excluded.name,
  description = excluded.description;

alter table public.octp_item_types enable row level security;
alter table public.octp_scraper_configs enable row level security;
alter table public.octp_scraper_logs enable row level security;

grant select, insert, update, delete on public.octp_item_types to authenticated;
grant select, insert, update, delete on public.octp_scraper_configs to authenticated;
grant select, insert, update, delete on public.octp_scraper_logs to authenticated;

drop policy if exists "octopus admin can read item types" on public.octp_item_types;
drop policy if exists "octopus admin can insert item types" on public.octp_item_types;
drop policy if exists "octopus admin can update item types" on public.octp_item_types;
drop policy if exists "octopus admin can delete item types" on public.octp_item_types;

create policy "octopus admin can read item types"
on public.octp_item_types
for select
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can insert item types"
on public.octp_item_types
for insert
to authenticated
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can update item types"
on public.octp_item_types
for update
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid)
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can delete item types"
on public.octp_item_types
for delete
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

drop policy if exists "octopus admin can read scraper configs" on public.octp_scraper_configs;
drop policy if exists "octopus admin can insert scraper configs" on public.octp_scraper_configs;
drop policy if exists "octopus admin can update scraper configs" on public.octp_scraper_configs;
drop policy if exists "octopus admin can delete scraper configs" on public.octp_scraper_configs;

create policy "octopus admin can read scraper configs"
on public.octp_scraper_configs
for select
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can insert scraper configs"
on public.octp_scraper_configs
for insert
to authenticated
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can update scraper configs"
on public.octp_scraper_configs
for update
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid)
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can delete scraper configs"
on public.octp_scraper_configs
for delete
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

drop policy if exists "octopus admin can read scraper logs" on public.octp_scraper_logs;
drop policy if exists "octopus admin can insert scraper logs" on public.octp_scraper_logs;
drop policy if exists "octopus admin can update scraper logs" on public.octp_scraper_logs;
drop policy if exists "octopus admin can delete scraper logs" on public.octp_scraper_logs;

create policy "octopus admin can read scraper logs"
on public.octp_scraper_logs
for select
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can insert scraper logs"
on public.octp_scraper_logs
for insert
to authenticated
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can update scraper logs"
on public.octp_scraper_logs
for update
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid)
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can delete scraper logs"
on public.octp_scraper_logs
for delete
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);
