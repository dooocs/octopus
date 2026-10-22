-- 动态 filter (2026-10-22), processing/feed_digest.py.
--
-- The site shows only standalone posts (no quotes, retweets or thread
-- fragments whose head was dropped), and an LLM label hides 政治 / 硬核 /
-- 广告 / 缺上下文 posts. Hidden posts never reach proc_feed_posts, so the site
-- build needs no change whichever key it reads with; they are kept in
-- proc_feed_hidden with the reason, for review in the admin.

alter table public.proc_feed_posts add column if not exists filter_version int;

create table if not exists public.proc_feed_hidden (
  id text primary key,                 -- same id as the post would have in proc_feed_posts
  author text not null,
  url text not null,
  published_at timestamptz not null,
  text_original text not null,
  reason text not null,                -- 非独立帖子 / 政治 / 硬核 / 广告 / 缺上下文
  content_hash text not null,
  filter_version int not null default 0,
  created_date timestamptz not null default now(),
  updated_date timestamptz not null default now()
);

create index if not exists proc_feed_hidden_published_at_idx
on public.proc_feed_hidden (published_at desc);

drop trigger if exists proc_feed_hidden_set_updated_date on public.proc_feed_hidden;
create trigger proc_feed_hidden_set_updated_date
before update on public.proc_feed_hidden
for each row execute function public.octp_set_updated_date();

-- Written by the service role (Octopus); readable by the Octopus admin only.
alter table public.proc_feed_hidden enable row level security;
grant select on public.proc_feed_hidden to authenticated;
drop policy if exists "octopus admin can read proc_feed_hidden" on public.proc_feed_hidden;
create policy "octopus admin can read proc_feed_hidden" on public.proc_feed_hidden
for select to authenticated using (auth.uid() = 'e3bffb6b-3a56-4c40-b47f-8bcb2de7916a'::uuid);

-- Turn the filter on. Posts already shown are classified once on the next run.
update public.octp_pipelines
set input = jsonb_set(
  input, '{filter}',
  jsonb_build_object('version', 1, 'hide', jsonb_build_array('政治', '硬核', '广告', '缺上下文'))
)
where slug = 'feed_digest';
