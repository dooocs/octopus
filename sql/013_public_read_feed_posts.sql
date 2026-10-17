-- The site build (ahaIndex2) reads proc_feed_posts to render /feed. Its build
-- key may be a publishable (anon) key, and these posts are public anyway: they
-- are published on www.amazingindex.com/feed. Read-only; writes stay with the
-- service role (Octopus).

drop policy if exists "public can read proc_feed_posts" on public.proc_feed_posts;
create policy "public can read proc_feed_posts" on public.proc_feed_posts
for select to anon using (true);

grant select on public.proc_feed_posts to anon;
