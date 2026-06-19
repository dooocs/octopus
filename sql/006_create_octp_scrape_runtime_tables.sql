-- Octopus scrape runtime tables for Supabase/Postgres.
-- New runner writes run/task/state here. Legacy octp_scraper_logs is retained
-- for history and is no longer the primary runtime target.

alter table public.octp_scraper_configs
add column if not exists input_schema_version int not null default 1;

create table if not exists public.octp_scrape_runs (
  id uuid primary key default gen_random_uuid(),

  snapshot_date date not null,
  trigger_type text not null default 'manual',
  trigger_ref text,

  status text not null default 'running'
    check (status in ('running', 'success', 'partial', 'failed', 'cancelled')),

  started_at timestamptz not null default now(),
  finished_at timestamptz,
  summary jsonb not null default '{}'::jsonb,

  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create table if not exists public.octp_scrape_tasks (
  id uuid primary key default gen_random_uuid(),

  run_id uuid references public.octp_scrape_runs(id) on delete cascade,
  scraper_config_id uuid references public.octp_scraper_configs(id) on delete set null,
  snapshot_date date not null,

  scraper text not null,
  sub_source_type text not null,

  status text not null default 'running'
    check (status in ('running', 'success', 'failed', 'partial', 'skipped')),
  stage text not null default 'validate_config'
    check (stage in (
      'validate_config',
      'discover',
      'filter',
      'enrich',
      'normalize',
      'validate_output',
      'sink',
      'done'
    )),

  config_snapshot jsonb not null default '{}'::jsonb,

  items_discovered int not null default 0,
  items_filtered int not null default 0,
  items_enriched int not null default 0,
  items_written int not null default 0,

  duration_ms int,
  error_message text,
  error_logs jsonb not null default '[]'::jsonb,

  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create table if not exists public.octp_scraper_state (
  scraper_config_id uuid primary key references public.octp_scraper_configs(id) on delete cascade,

  state jsonb not null default '{}'::jsonb,
  last_success_snapshot_date date,
  last_success_run_id uuid references public.octp_scrape_runs(id) on delete set null,

  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create index if not exists octp_scrape_runs_snapshot_date_idx
on public.octp_scrape_runs (snapshot_date desc, created_date desc);

create index if not exists octp_scrape_runs_status_idx
on public.octp_scrape_runs (status, created_date desc);

create index if not exists octp_scrape_tasks_run_id_idx
on public.octp_scrape_tasks (run_id, created_date);

create index if not exists octp_scrape_tasks_config_date_idx
on public.octp_scrape_tasks (scraper_config_id, snapshot_date desc);

create index if not exists octp_scrape_tasks_status_date_idx
on public.octp_scrape_tasks (status, created_date desc);

drop trigger if exists octp_scrape_runs_set_updated_date on public.octp_scrape_runs;
create trigger octp_scrape_runs_set_updated_date
before update on public.octp_scrape_runs
for each row execute function public.octp_set_updated_date();

drop trigger if exists octp_scrape_tasks_set_updated_date on public.octp_scrape_tasks;
create trigger octp_scrape_tasks_set_updated_date
before update on public.octp_scrape_tasks
for each row execute function public.octp_set_updated_date();

drop trigger if exists octp_scraper_state_set_updated_date on public.octp_scraper_state;
create trigger octp_scraper_state_set_updated_date
before update on public.octp_scraper_state
for each row execute function public.octp_set_updated_date();

alter table public.octp_scrape_runs enable row level security;
alter table public.octp_scrape_tasks enable row level security;
alter table public.octp_scraper_state enable row level security;

grant select, insert, update, delete on public.octp_scrape_runs to authenticated;
grant select, insert, update, delete on public.octp_scrape_tasks to authenticated;
grant select, insert, update, delete on public.octp_scraper_state to authenticated;

drop policy if exists "octopus admin can read scrape runs" on public.octp_scrape_runs;
drop policy if exists "octopus admin can insert scrape runs" on public.octp_scrape_runs;
drop policy if exists "octopus admin can update scrape runs" on public.octp_scrape_runs;
drop policy if exists "octopus admin can delete scrape runs" on public.octp_scrape_runs;

create policy "octopus admin can read scrape runs"
on public.octp_scrape_runs
for select
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can insert scrape runs"
on public.octp_scrape_runs
for insert
to authenticated
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can update scrape runs"
on public.octp_scrape_runs
for update
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid)
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can delete scrape runs"
on public.octp_scrape_runs
for delete
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

drop policy if exists "octopus admin can read scrape tasks" on public.octp_scrape_tasks;
drop policy if exists "octopus admin can insert scrape tasks" on public.octp_scrape_tasks;
drop policy if exists "octopus admin can update scrape tasks" on public.octp_scrape_tasks;
drop policy if exists "octopus admin can delete scrape tasks" on public.octp_scrape_tasks;

create policy "octopus admin can read scrape tasks"
on public.octp_scrape_tasks
for select
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can insert scrape tasks"
on public.octp_scrape_tasks
for insert
to authenticated
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can update scrape tasks"
on public.octp_scrape_tasks
for update
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid)
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can delete scrape tasks"
on public.octp_scrape_tasks
for delete
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

drop policy if exists "octopus admin can read scraper state" on public.octp_scraper_state;
drop policy if exists "octopus admin can insert scraper state" on public.octp_scraper_state;
drop policy if exists "octopus admin can update scraper state" on public.octp_scraper_state;
drop policy if exists "octopus admin can delete scraper state" on public.octp_scraper_state;

create policy "octopus admin can read scraper state"
on public.octp_scraper_state
for select
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can insert scraper state"
on public.octp_scraper_state
for insert
to authenticated
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can update scraper state"
on public.octp_scraper_state
for update
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid)
with check (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

create policy "octopus admin can delete scraper state"
on public.octp_scraper_state
for delete
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);
