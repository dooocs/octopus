# Octopus

Octopus is a general-purpose crawler foundation. Its final output contract is a
single `raw_items` table designed for downstream consumers: stable item identity,
source metadata, time fields, main content, and structured context.

The current schema discussion lives in:

- [docs/raw-items-output-schema.html](docs/raw-items-output-schema.html)

## Migrated Scrapers

The initial crawler engines were migrated from `ahaIndexSync` without moving the
Supabase pipeline, LLM processing, ranking, enrichment, or public-site stages.

Current engines:

- `github_trending`
- `github_search`
- `hackernews`
- `rss`
- `twitter_twscrape`
- `ai_blog`
- `community_v2ex`
- `community_linuxdo`
- `reddit`
- `huggingface`
- `product_hunt`

## Local Run

Install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install .
```

Run enabled Supabase scraper configs through the same runner used by the
`Global Scrape` Action:

```bash
python -m scripts.global_scrape --continue-on-error
```

Write JSONL to a file:

```bash
python -m scripts.global_scrape --continue-on-error --output outputs/global_scrape/raw_items.jsonl
```

## DAO Layer

Database writes live under `infra/dao/`. Callers should use `OctopusDao` as the
single DAO entry point instead of importing table-specific DAO classes in
business code.

Aliyun RDS MySQL environment variables:

```bash
OCTOPUS_RDS_HOST=
OCTOPUS_RDS_PORT=3306
OCTOPUS_RDS_USER=
OCTOPUS_RDS_PASSWORD=
OCTOPUS_RDS_DATABASE=
OCTOPUS_RDS_CHARSET=utf8mb4
OCTOPUS_RDS_CONNECT_TIMEOUT=10
OCTOPUS_RDS_SSL_CA=
```

Use [.env.example](.env.example) as the local template. `OCTOPUS_RDS_SSL_CA` is
optional and only needed when the RDS instance requires SSL verification.

Example:

```python
from infra.dao import OctopusDao, RawItemRecord

row = RawItemRecord.from_mapping(raw_item_output)

with OctopusDao.from_env() as dao:
    dao.upsert_raw_item(row)
```

Run scrapers and write final output rows to RDS:

```bash
python -m scripts.global_scrape --continue-on-error --write-rds
```

## Web Admin

The React admin site lives in [`web/`](web/). It is a Vite app intended to be
deployed to Vercel with `web` as the project root directory.

It uses Supabase Auth with GitHub OAuth, matching the existing `ahaAdmin`
pattern. It defaults to the same Supabase project and publishable key used by
`ahaAdmin`; Vercel environment variables can override them:

```bash
VITE_SUPABASE_URL=https://wyhpcfjtmtitorinkevj.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_Mhngg1gf4z4dkj-xh5TsMg_Pz3crwfo
VITE_ALLOWED_GITHUB_LOGIN=walihome
VITE_ALLOWED_SUPABASE_USER_ID=
```

`VITE_ALLOWED_SUPABASE_USER_ID` is optional but recommended for deployment
because a Supabase user UUID is more stable than GitHub profile metadata. Do not
put a Supabase service-role key in this frontend.

Supabase does not need a separate install step for this web app. Make sure the
GitHub Auth provider is enabled, the Auth redirect URL allow list includes the
Vercel domain and `http://localhost:5173`, and the Octopus admin tables
`octp_item_types`, `octp_scraper_configs`, and `octp_scraper_logs` remain
exposed with RLS policies that restrict reads/writes to the single allowed user.

Local development:

```bash
cd web
npm install
npm run dev
```

Build verification:

```bash
cd web
npm run build
```

The first screen groups all scraper configs by `item_type` on the left and shows
the selected rows from `octp_scraper_configs` on the right. Creating or editing
rows writes to the Supabase `octp_scraper_configs` table. Managed all-source runs
use the trusted GitHub Action described below.

There is also a manual GitHub Action named `aliyun_rds_test`. It does not run
real crawlers. It upserts one deterministic smoke-test row into `raw_items` so
you can validate RDS connectivity, credentials, table structure, and DAO writes.
The workflow calls the installed console script `octopus-aliyun-rds-test`, so it
does not depend on ad hoc `PYTHONPATH` settings.

## Global Scrape Action

The manual GitHub Action `Global Scrape` reads enabled rows from Supabase
`octp_scraper_configs`, writes one row per scraper execution into
`octp_scraper_logs`, runs the supported Octopus scraper engines, and can upsert
the resulting rows into the Aliyun RDS `raw_items` table.

Required GitHub secrets:

```bash
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
OCTOPUS_RDS_HOST=
OCTOPUS_RDS_PORT=3306
OCTOPUS_RDS_USER=
OCTOPUS_RDS_PASSWORD=
OCTOPUS_RDS_DATABASE=
OCTOPUS_RDS_CHARSET=utf8mb4
OCTOPUS_RDS_CONNECT_TIMEOUT=10
OCTOPUS_RDS_SSL_CA=
```

Provider secrets reused from `ahaIndexSync` where applicable:

```bash
GH_MODELS_TOKEN=
KIMI_API_KEY=
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
TWITTERAPI_IO_KEY=
PRODUCTHUNT_TOKEN=
```

Manual local equivalent:

```bash
python -m scripts.global_scrape --continue-on-error --write-rds
```

## Output Table

The final output table intentionally ignores crawler internals such as retries,
request logs, fetch status, temporary payloads, and debugging traces.

Core fields:

- `id`
- `url`
- `source_type`
- `sub_source_type`
- `item_type`
- `author_id`
- `author_url`
- `created_date`
- `updated_date`
- `source_published_date`
- `snapshot_date`
- `title`
- `metrics`
- `content`
- `context_content`
- `extra`
- `scrape_config_snapshot`

`content` is the primary text downstream consumers can index, summarize, or
vectorize by default. `context_content` is structured JSON for supporting
context such as summaries, comments, quoted posts, threads, README details,
discussion URLs, or product metadata.
