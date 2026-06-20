insert into public.octp_item_types (item_type, name, description) values
  ('release', 'Release', 'Software releases and changelog entries'),
  ('package_release', 'Package Release', 'Package registry release events')
on conflict (item_type) do update
set
  name = excluded.name,
  description = excluded.description;
