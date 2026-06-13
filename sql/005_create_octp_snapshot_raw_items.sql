-- Octopus frontend snapshot table for Supabase/Postgres.
-- This table is overwritten by the Global Scrape Action with one snapshot_date
-- of Aliyun RDS raw_items data for frontend testing.

create table if not exists public.octp_snapshot_raw_items (
  id text primary key,
  url text not null,

  source_type text not null,
  sub_source_type text not null,
  item_type text not null,
  author_id text,
  author_url text,

  created_date timestamp(3) without time zone not null,
  updated_date timestamp(3) without time zone not null,
  source_published_date timestamp(3) without time zone,
  snapshot_date date not null,

  title text not null,
  metrics jsonb,
  content text,
  context_content jsonb,
  extra jsonb,
  scrape_config_snapshot jsonb,

  constraint octp_snapshot_raw_items_id_length check (char_length(id) = 32)
);

create index if not exists octp_snapshot_raw_items_snapshot_date_idx
on public.octp_snapshot_raw_items (snapshot_date);

create index if not exists octp_snapshot_raw_items_source_snapshot_idx
on public.octp_snapshot_raw_items (source_type, sub_source_type, snapshot_date);

create index if not exists octp_snapshot_raw_items_item_type_snapshot_idx
on public.octp_snapshot_raw_items (item_type, snapshot_date);

create index if not exists octp_snapshot_raw_items_source_published_date_idx
on public.octp_snapshot_raw_items (source_published_date);

create index if not exists octp_snapshot_raw_items_author_id_idx
on public.octp_snapshot_raw_items (author_id);

alter table public.octp_snapshot_raw_items enable row level security;

grant select on public.octp_snapshot_raw_items to authenticated;
grant select, insert, update, delete on public.octp_snapshot_raw_items to service_role;

drop policy if exists "octopus admin can read raw item snapshots"
on public.octp_snapshot_raw_items;

create policy "octopus admin can read raw item snapshots"
on public.octp_snapshot_raw_items
for select
to authenticated
using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);
