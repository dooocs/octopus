-- Supabase RLS template for Octopus Web Admin.
--
-- 1. Sign in once with the allowed GitHub account.
-- 2. Find that Supabase auth user UUID:
--
--    select
--      id,
--      raw_user_meta_data ->> 'user_name' as github_login,
--      email
--    from auth.users
--    order by created_at desc;
--
-- 3. Replace 00000000-0000-0000-0000-000000000000 below with that UUID.
--
-- Do not use raw_user_meta_data in RLS policies. It is user-editable metadata.

alter table public.scraper_configs enable row level security;

grant select, insert, update, delete on public.scraper_configs to authenticated;

drop policy if exists "octopus admin can read scraper configs" on public.scraper_configs;
drop policy if exists "octopus admin can insert scraper configs" on public.scraper_configs;
drop policy if exists "octopus admin can update scraper configs" on public.scraper_configs;
drop policy if exists "octopus admin can delete scraper configs" on public.scraper_configs;

create policy "octopus admin can read scraper configs"
on public.scraper_configs
for select
to authenticated
using (auth.uid() = '00000000-0000-0000-0000-000000000000'::uuid);

create policy "octopus admin can insert scraper configs"
on public.scraper_configs
for insert
to authenticated
with check (auth.uid() = '00000000-0000-0000-0000-000000000000'::uuid);

create policy "octopus admin can update scraper configs"
on public.scraper_configs
for update
to authenticated
using (auth.uid() = '00000000-0000-0000-0000-000000000000'::uuid)
with check (auth.uid() = '00000000-0000-0000-0000-000000000000'::uuid);

create policy "octopus admin can delete scraper configs"
on public.scraper_configs
for delete
to authenticated
using (auth.uid() = '00000000-0000-0000-0000-000000000000'::uuid);
