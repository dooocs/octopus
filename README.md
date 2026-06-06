# Octopus

Octopus is a general-purpose crawler foundation. Its final output contract is a
single `raw_items` table designed for downstream consumers: stable item identity,
source metadata, time fields, main content, and structured context.

The current schema discussion lives in:

- [docs/raw-items-output-schema.html](docs/raw-items-output-schema.html)

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
