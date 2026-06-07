-- Normalize Octopus item types after the initial scraper admin table rollout.
-- `post` is the generic social-post item type; `discussion` covers community
-- topics/threads such as Reddit, V2EX, LinuxDo, and Hacker News discussions.

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

delete from public.octp_item_types
where item_type = 'tweet'
  and not exists (
    select 1
    from public.octp_scraper_configs
    where item_type = 'tweet'
  );
