"""Hands one day's octopus rows to ahaIndexSync's daily pipeline.

The daily briefing (AI 日报) is still processed and ranked by ahaIndexSync,
which reads its own Supabase `raw_items` + `items_content` for a snapshot_date.
Sources that only octopus scrapes reach the briefing through this pipeline:
it copies the selected sub_source_types' rows for today's snapshot_date into
those two tables in ahaIndexSync's shape, before ahaIndexSync's 02:00 run.

This is the anti-corruption layer between the two systems and the only place
that knows ahaIndexSync's row shape. It goes away when octopus takes over
processing.

Config (octp_pipelines.input):
    {
      "timezone": "Asia/Shanghai",            # snapshot_date is this zone's today
      "target": {"raw_table": "raw_items", "content_table": "items_content"},
      "sources": {
        "<sub_source_type>": {"source_name": "...", "source_type": "...", "content_type": "..."}
      }
    }

ahaIndexSync's raw_items.scraper_slug is NOT NULL with a foreign key to its
scraper_configs.slug, so every handed-off row points at one disabled marker
config, slug "octopus" (sql/017). ahaIndexSync only loads enabled configs, so it
never runs it. Which octopus source a row came from is in extra.octopus.

Rows that ahaIndexSync's own scrapers already wrote (same id, any other
scraper_slug) are left untouched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from processing.core import Deps, PipelineConfig, PipelineIncomplete, register_pipeline

KIND = "aha_daily_handoff"
SCRAPER_SLUG = "octopus"  # the disabled marker row in ahaIndexSync scraper_configs (sql/017)
BODY_LIMIT = 12000


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_body(row: dict[str, Any]) -> str:
    """ahaIndexSync's process stage reads one text body; fold the context into it."""
    parts = [str(row.get("content") or "").strip()]
    context = row.get("context_content") or {}
    comments = context.get("top_comments") if isinstance(context, dict) else None
    if isinstance(comments, list) and comments:
        lines = []
        for comment in comments[:10]:
            if not isinstance(comment, dict):
                continue
            text = str(comment.get("text") or "").strip()
            if text:
                author = str(comment.get("author") or "").strip()
                lines.append(f"- {author + ': ' if author else ''}{text[:600]}")
        if lines:
            parts.append("Top comments:\n" + "\n".join(lines))
    body = "\n\n".join(part for part in parts if part)
    return body[:BODY_LIMIT] or str(row.get("title") or "")


def to_sync_row(row: dict[str, Any], mapping: dict[str, Any], snapshot_date: str) -> dict[str, Any]:
    sub_source_type = str(row.get("sub_source_type") or "")
    extra = dict(row.get("extra") or {})
    extra.setdefault("source_tag", sub_source_type)
    extra["octopus"] = {"raw_item_id": row.get("id"), "sub_source_type": sub_source_type, "item_type": row.get("item_type")}
    return {
        "id": row["id"],
        "title": str(row.get("title") or ""),
        "original_url": str(row.get("url") or ""),
        "source_name": str(mapping["source_name"]),
        "source_type": str(mapping.get("source_type") or row.get("source_type") or ""),
        "content_type": str(mapping["content_type"]),
        "author": str(row.get("author_id") or ""),
        "author_url": str(row.get("author_url") or ""),
        "raw_metrics": dict(row.get("metrics") or {}),
        "extra": extra,
        "published_at": _iso(row.get("source_published_date")),
        "snapshot_date": snapshot_date,
        "scraper_slug": SCRAPER_SLUG,
        "scraper_config_snapshot": dict(row.get("scrape_config_snapshot") or {}),
    }


def _chunks(values: list[str], size: int = 100):
    for start in range(0, len(values), size):
        yield values[start:start + size]


@register_pipeline(KIND)
def run_aha_daily_handoff(config: PipelineConfig, deps: Deps) -> dict[str, Any]:
    sources = config.input.get("sources") or {}
    if not isinstance(sources, dict) or not sources:
        raise ValueError(f"{KIND} needs input.sources: {{sub_source_type: {{source_name, content_type}}}}")
    for sub_source_type, mapping in sources.items():
        if not isinstance(mapping, dict) or not mapping.get("source_name") or not mapping.get("content_type"):
            raise ValueError(f"{KIND} input.sources.{sub_source_type} needs source_name and content_type")
    target = config.input.get("target") or {}
    raw_table = deps.table(str(target.get("raw_table") or "raw_items"))
    content_table = deps.table(str(target.get("content_table") or "items_content"))
    zone = ZoneInfo(str(config.input.get("timezone") or "Asia/Shanghai"))
    snapshot_date = deps.now().astimezone(zone).date().isoformat()

    rows = deps.raw_items.select_by_snapshot_date(list(sources), snapshot_date)
    ids = [str(row["id"]) for row in rows]
    owned_elsewhere: set[str] = set()
    for chunk in _chunks(ids):
        for existing in raw_table.select({"id": f"in.({','.join(chunk)})", "select": "id,scraper_slug"}):
            slug = str(existing.get("scraper_slug") or "")
            if slug != SCRAPER_SLUG:
                owned_elsewhere.add(str(existing["id"]))

    raw_rows: list[dict[str, Any]] = []
    content_rows: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    for row in rows:
        if str(row["id"]) in owned_elsewhere or not row.get("url") or not row.get("title"):
            continue
        sub_source_type = str(row.get("sub_source_type") or "")
        raw_rows.append(to_sync_row(row, sources[sub_source_type], snapshot_date))
        content_rows.append({"item_id": row["id"], "raw_body": build_body(row)})
        per_source[sub_source_type] = per_source.get(sub_source_type, 0) + 1

    errors: list[str] = []
    try:
        raw_table.upsert(raw_rows, on_conflict="id")
    except Exception as exc:  # nothing reached ahaIndexSync
        raise RuntimeError(f"writing {len(raw_rows)} rows to raw_items failed: {exc}") from exc
    try:
        content_table.upsert(content_rows, on_conflict="item_id")
    except Exception as exc:
        errors.append(f"items_content: {exc}")

    summary = {
        "snapshot_date": snapshot_date,
        "read": len(rows),
        "handed_off": len(raw_rows),
        "skipped_owned_by_sync": len(owned_elsewhere),
        "per_source": per_source,
        "sources_without_rows": sorted(set(sources) - set(per_source)),
    }
    if errors:
        summary["errors"] = errors
        raise PipelineIncomplete("; ".join(errors), summary, partial=True)
    return summary
