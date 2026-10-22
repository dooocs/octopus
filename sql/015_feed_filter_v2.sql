-- 动态 filter v2 (2026-10-22): sharper 政治 (attacks on media or groups) and
-- 广告 (the author's own or joke launches are kept) definitions in
-- FILTER_SYSTEM; a lone number or emoji is 缺上下文. Version 2 relabels every
-- post in the window once; translations are reused.

update public.octp_pipelines
set input = jsonb_set(input, '{filter,version}', '2'::jsonb)
where slug = 'feed_digest';
