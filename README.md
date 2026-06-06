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
pip install -r requirements.txt
```

Run enabled scrapers from the example config and print JSONL:

```bash
python main.py --config configs/scrapers.example.json --date 2026-06-06
```

Write JSONL to a file:

```bash
python main.py --config configs/scrapers.example.json --output outputs/raw_items.jsonl
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
