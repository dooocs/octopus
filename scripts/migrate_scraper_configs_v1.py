from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:
        return None

from crawler.core.registry import get_adapter, list_types
from crawler.core.validation import validate_input
from infra.supabase import SupabaseRestClient

INPUT_KEYS = ("source", "fetch", "filters", "enrich")
LEGACY_INPUT_KEYS = {"source", "fetch", "filters", "enrich", "runtime"}


@dataclass(frozen=True)
class MigrationResult:
    config_id: str | None
    name: str
    scraper: str
    status: str
    input_value: dict[str, Any] | None = None
    error: str | None = None


def is_v1_input(value: dict[str, Any]) -> bool:
    return set(value.keys()) == set(INPUT_KEYS)


def convert_flat_input(scraper: str, raw: dict[str, Any]) -> dict[str, Any]:
    if is_v1_input(raw):
        return raw
    if set(raw.keys()) == LEGACY_INPUT_KEYS:
        return {key: raw[key] for key in INPUT_KEYS}

    source: dict[str, Any] = {}
    fetch: dict[str, Any] = {}
    filters: dict[str, Any] = {}
    enrich: list[dict[str, Any]] = []

    def move(keys: list[str], target: dict[str, Any]) -> None:
        for key in keys:
            if key in raw:
                target[key] = raw[key]

    if scraper == "rss":
        move(["url"], source)
        move(["max_items", "fetch_window_hours"], fetch)
    elif scraper == "ai_blog":
        move(["base_url", "news_url", "link_selector", "author", "source_tag"], source)
        move(["fetch_window_hours"], fetch)
    elif scraper == "github_trending":
        move(["timeout"], fetch)
        enrich = [
            {"name": "github_readme", "when": "always"},
            {"name": "github_languages", "when": "always"},
            {"name": "github_images", "when": "has_readme"},
            {"name": "star_history", "when": "always"},
        ]
    elif scraper == "github_search":
        move(["queries"], source)
        move(["per_page", "fetch_window_days", "max_readme_images", "badge_patterns"], fetch)
        move(["min_stars"], filters)
        enrich = [
            {"name": "github_readme", "when": "always"},
            {"name": "github_languages", "when": "always"},
            {"name": "github_images", "when": "has_readme"},
            {"name": "star_history", "when": "always"},
        ]
    elif scraper == "hackernews":
        source["feed"] = raw.get("feed", "newstories")
        move(["new_n", "cutoff_hours", "fetch_workers", "skip_domains"], fetch)
        move(["min_score"], filters)
        enrich = [{"name": "article_body", "when": "has_external_url"}]
    elif scraper == "twitter_twscrape":
        move(["watch_accounts", "tracked_keywords"], source)
        move(["max_age_days"], fetch)
        move(["timeline_min_faves", "min_likes"], filters)
    elif scraper == "community_v2ex":
        move(["source_tag"], source)
        move(["top_n", "max_replies_to_fetch", "max_replies_to_keep"], fetch)
        enrich = [{"name": "top_replies", "when": "always"}]
    elif scraper == "community_linuxdo":
        move(["source_tag"], source)
        move(["top_n", "max_replies_to_fetch"], fetch)
        enrich = [{"name": "top_replies", "when": "always"}]
    elif scraper == "reddit":
        move(["subreddit"], source)
        move(["max_retries"], fetch)
        move(
            [
                "min_score",
                "skip_nsfw",
                "skip_stickied",
                "skip_discussion_below",
                "skip_self_text_below",
            ],
            filters,
        )
    elif scraper == "hf_model":
        move(["limit", "max_retries"], fetch)
        move(["min_likes", "min_downloads", "quant_suffixes", "deriv_suffixes"], filters)
    elif scraper == "hf_papers":
        move(["top_n", "max_retries"], fetch)
    elif scraper == "product_hunt":
        move(["api_token"], source)
        move(["max_retries"], fetch)
        move(["min_votes", "topic_whitelist", "topic_blacklist"], filters)
    else:
        raise ValueError(f"unsupported scraper: {scraper}")

    return {
        "source": source,
        "fetch": fetch,
        "filters": filters,
        "enrich": enrich,
    }


def migrate_row(row: dict[str, Any]) -> MigrationResult:
    scraper = str(row.get("scraper") or "")
    adapter = get_adapter(scraper)
    name = str(row.get("name") or "")
    config_id = str(row["id"]) if row.get("id") else None
    if adapter is None:
        return MigrationResult(config_id, name, scraper, "unsupported", error=f"unsupported scraper: {scraper}")

    raw_input = row.get("input") or {}
    if not isinstance(raw_input, dict):
        return MigrationResult(config_id, name, scraper, "failed", error="input must be a JSON object")

    try:
        converted = convert_flat_input(scraper, raw_input)
        validate_input(adapter.spec, converted)
        return MigrationResult(config_id, name, scraper, "converted", input_value=converted)
    except Exception as exc:
        return MigrationResult(config_id, name, scraper, "failed", error=str(exc))


def run_migration(*, apply: bool, client: SupabaseRestClient | None = None) -> int:
    supabase = client or SupabaseRestClient.from_env()
    rows = supabase.fetch_config_rows()
    results = [migrate_row(row) for row in rows]

    converted = [item for item in results if item.status == "converted"]
    unsupported = [item for item in results if item.status == "unsupported"]
    failed = [item for item in results if item.status == "failed"]

    print(
        "scraper config migration summary: "
        f"total={len(results)} converted={len(converted)} "
        f"unsupported={len(unsupported)} failed={len(failed)} supported={','.join(list_types())}"
    )

    for item in unsupported + failed:
        print(f"  {item.status}: id={item.config_id} name={item.name} scraper={item.scraper} error={item.error}")

    if unsupported or failed:
        return 1

    if apply:
        for item in converted:
            if not item.config_id or item.input_value is None:
                continue
            supabase.update_scraper_config_input(
                item.config_id,
                input_value=item.input_value,
                input_schema_version=1,
            )
        print(f"applied scraper config migration: {len(converted)} rows")
    else:
        print(json.dumps([item.input_value for item in converted[:3]], ensure_ascii=False, indent=2))
        print("dry run only; pass --apply to update Supabase")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate Octopus scraper configs to v1 four-part input")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Validate and print summary only")
    group.add_argument("--apply", action="store_true", help="Update Supabase configs")
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    raise SystemExit(run_migration(apply=bool(args.apply)))


if __name__ == "__main__":
    main()
