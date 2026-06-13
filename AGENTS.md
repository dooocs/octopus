# Octopus Agent Guide

This file applies to the `octopus` repository only. The parent
`amazing-sync` directory contains multiple independent repositories, so always
run git and verification commands from this repo root unless the task
explicitly says otherwise.

## Project Shape

- `scrapers/`: source-specific crawler engines. Keep source-specific request,
  parsing, and normalization logic here.
- `infra/models.py`: shared output model for crawler rows.
- `infra/dao/`: Aliyun RDS MySQL access. Use `OctopusDao` as the business-code
  entry point instead of importing table-specific DAO classes directly.
- `scripts/global_scrape.py`: managed scrape runner used by the `Global Scrape`
  GitHub Action. It reads enabled scraper configs from Supabase and can write
  final rows to RDS.
- `scripts/octp_supabase.py`: Supabase access for Octopus admin config and log
  tables.
- `sql/`: raw item and Octopus admin table migrations.
- `web/`: React + Vite admin UI for scraper config and run management.
- `tests/`: Python unit tests for models, DAO mapping, scripts, and Supabase
  helpers.

## Core Contracts

- The stable downstream output boundary is `raw_items`.
- `content` is the primary text field for downstream indexing, summarization,
  and vectorization.
- `context_content` is structured JSON for supporting context such as comments,
  README details, discussion URLs, product metadata, quoted posts, or thread
  summaries.
- Crawler internals such as retries, request logs, temporary payloads, and
  debug traces should not be added to `raw_items`. Put orchestration or
  observability state in separate run/task/log tables.
- Managed scraper configuration is read from Supabase
  `octp_scraper_configs`; do not add committed production config files unless
  the task specifically asks for them.

## Python Commands

Use Python 3.12 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install .
```

Run the managed scrape path without RDS writes:

```bash
python -m scripts.global_scrape --continue-on-error
```

Run it with Aliyun RDS writes:

```bash
python -m scripts.global_scrape --continue-on-error --write-rds
```

Run verification:

```bash
python -m compileall infra scrapers scripts tests
python -m unittest discover tests
```

## Web Commands

The admin UI is under `web/`.

```bash
cd web
npm install
npm run dev
```

Build/type-check verification:

```bash
cd web
npm run build
```

## Environment And Secrets

- Use `.env.example` and `web/.env.example` as templates.
- Never commit real API keys, Supabase service-role keys, RDS credentials,
  provider tokens, OSS credentials, or OAuth secrets.
- Backend and Actions code may use `SUPABASE_SERVICE_ROLE_KEY` and RDS
  credentials.
- Frontend code under `web/` must only use publishable Supabase values. Never
  expose service-role or secret keys through Vite environment variables.
- Optional provider secrets include `GH_MODELS_TOKEN`, `KIMI_API_KEY`,
  `OSS_ACCESS_KEY_ID`, `OSS_ACCESS_KEY_SECRET`, `TWITTERAPI_IO_KEY`, and
  `PRODUCTHUNT_TOKEN`.

## Implementation Preferences

- Keep scraper changes idempotent where possible. Repeated runs for the same
  `snapshot_date` should not create duplicate logical items.
- Preserve the existing source registry pattern in `scrapers/registry.py` when
  adding or renaming scraper engines.
- Keep SQL migrations, Python models, DAO mappings, Supabase helpers, and web
  TypeScript types aligned when changing table or payload shapes.
- Prefer narrow, source-specific tests for scraper changes and broader model or
  DAO tests when changing shared output contracts.
- Match the admin UI's operational style: compact controls, clear status, and
  direct table-management workflows.

## Git Workflow

- Check `git status -sb` before editing. If the worktree contains unrelated
  user changes, keep them out of your branch and staging area.
- Use `codex/<description>` branch names for Codex-created work unless the user
  asks for another name.
- Stage explicit files when the worktree is mixed.
- Before opening a PR, run the narrowest meaningful verification command and
  report any command that could not run.
