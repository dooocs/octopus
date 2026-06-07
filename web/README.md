# Octopus Web Admin

React + Vite management UI for Octopus scraper configs.

## Local Development

The app defaults to the same Supabase project and publishable key used by
`ahaAdmin`, so it can run locally without a `.env.local` file:

```bash
npm install
npm run dev
```

Optional local overrides:

```bash
VITE_SUPABASE_URL=
VITE_SUPABASE_PUBLISHABLE_KEY=
VITE_ALLOWED_GITHUB_LOGIN=walihome
VITE_ALLOWED_SUPABASE_USER_ID=
```

`VITE_ALLOWED_SUPABASE_USER_ID` is recommended in production because the
Supabase auth user UUID is a stronger gate than GitHub profile metadata.

## Supabase Setup

This app does not need to be installed into Supabase. It uses the existing
Supabase project over the public Data API.

Required Supabase pieces:

- GitHub Auth provider enabled.
- GitHub OAuth callback URL set to `https://<project-ref>.supabase.co/auth/v1/callback`.
- Supabase Auth URL configuration includes the deployed Vercel domain and local
  dev URL, for example `http://localhost:5173`.
- Existing `public.scraper_configs` table from `ahaIndexSync/sql/001_config_tables.sql`.
- RLS policies should restrict `scraper_configs` reads/writes to the one
  allowed authenticated user. The client-side gate is for UX only. Use
  [`docs/supabase-scraper-configs-rls.sql`](docs/supabase-scraper-configs-rls.sql)
  as the SQL template.

## Vercel

Create a Vercel project with:

- Root Directory: `web`
- Build Command: `npm run build`
- Output Directory: `dist`
- Framework Preset: Vite

Environment variables are optional when using the default project, but setting
them explicitly in Vercel is clearer:

```bash
VITE_SUPABASE_URL=https://wyhpcfjtmtitorinkevj.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_Mhngg1gf4z4dkj-xh5TsMg_Pz3crwfo
VITE_ALLOWED_GITHUB_LOGIN=walihome
VITE_ALLOWED_SUPABASE_USER_ID=
```

Never use a Supabase service-role or secret key in this frontend.
