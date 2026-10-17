-- 动态 processing is translation only (2026-10-17): no per-author 24h summary.
--
-- proc_feed_authors held the summary plus the card's name and avatar. The
-- name and avatar move onto proc_feed_posts, so the site reads one table and
-- groups posts by author itself (cards ordered by each author's newest post).
--
-- proc_feed_posts.text_zh = '' means no translation is needed (already
-- Chinese, or only links/mentions/media): the site shows the original alone.
-- Otherwise the site shows the original with text_zh below it.

alter table public.proc_feed_posts add column if not exists display_name text;
alter table public.proc_feed_posts add column if not exists avatar_url text;

create index if not exists proc_feed_posts_author_updated_at_idx
on public.proc_feed_posts (author, updated_at desc);

drop table if exists public.proc_feed_authors;

-- New prompt (no "keep Chinese as is" rule; Chinese posts skip the LLM): any
-- stored translation is regenerated once.
update public.octp_pipelines
set name = '动态 · 翻译',
    input = jsonb_set(input, '{prompt_version}', '2'::jsonb)
where slug = 'feed_digest';
