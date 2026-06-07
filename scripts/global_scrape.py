from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from infra.dao import OctopusDao, RawItemRecord
from infra.scraper_runner import run_scrapers
from scrapers.registry import list_types
from scripts.octp_supabase import (
    SupabaseRestClient,
    load_enabled_runtime_configs,
    split_supported_configs,
)


def _github_run_context() -> dict[str, Any]:
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "")

    run_url = ""
    if repository and run_id:
        run_url = f"{server_url}/{repository}/actions/runs/{run_id}"

    return {
        "github_run_id": run_id or None,
        "github_run_attempt": run_attempt or None,
        "github_run_url": run_url or None,
        "github_workflow": os.getenv("GITHUB_WORKFLOW") or None,
        "github_ref": os.getenv("GITHUB_REF") or None,
        "github_sha": os.getenv("GITHUB_SHA") or None,
        "github_actor": os.getenv("GITHUB_ACTOR") or None,
    }


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str))
            f.write("\n")


def _create_log(
    client: SupabaseRestClient,
    config: dict[str, Any],
    snapshot_date: str,
    run_context: dict[str, Any],
) -> str | None:
    return client.create_scraper_log(
        {
            "scraper_config_id": config.get("id"),
            "snapshot_date": snapshot_date,
            "github_run_id": run_context.get("github_run_id"),
            "config_snapshot": {
                "id": config.get("id"),
                "name": config.get("name"),
                "scraper": config.get("type"),
                "enabled": config.get("enabled"),
                "priority": config.get("priority"),
                "source_type": config.get("source_type"),
                "sub_source_type": config.get("sub_source_type"),
                "item_type": config.get("item_type"),
                "input": config.get("config") or {},
            },
            "status": "running",
            "result": run_context,
            "error_logs": [],
        }
    )


def _update_log_safe(
    client: SupabaseRestClient,
    log_id: str | None,
    payload: dict[str, Any],
) -> None:
    if not log_id:
        return
    try:
        client.update_scraper_log(log_id, payload)
    except Exception as exc:
        print(f"  ⚠️ 更新 scraper log 失败: {exc}")


def _write_rows_to_rds(rows: list[dict[str, Any]]) -> int:
    records = [RawItemRecord.from_mapping(row) for row in rows]
    if not records:
        return 0
    with OctopusDao.from_env() as dao:
        return dao.raw_items.upsert_many(records)


def run_global_scrape(args: argparse.Namespace) -> int:
    client = SupabaseRestClient.from_env()
    run_context = _github_run_context()
    supported_types = list_types()
    configs, skipped = split_supported_configs(
        load_enabled_runtime_configs(client),
        supported_types,
    )

    print(f"loaded enabled configs: runnable={len(configs)}, skipped={len(skipped)}")
    if skipped:
        skipped_names = ", ".join(f"{c['name']}({c['type']})" for c in skipped)
        print(f"skipped unsupported scraper configs: {skipped_names}")

    output_path = Path(args.output) if args.output else None
    if output_path and output_path.exists():
        output_path.unlink()

    total_items = 0
    total_affected = 0
    failed = 0

    for config in configs:
        print(f"running scraper: {config['name']} [{config['type']}]")
        started = time.monotonic()
        log_id = _create_log(client, config, args.date, run_context)

        try:
            rows = run_scrapers([config], args.date)
            affected = _write_rows_to_rds(rows) if args.write_rds else 0
            if output_path:
                _append_jsonl(output_path, rows)

            duration_ms = int((time.monotonic() - started) * 1000)
            total_items += len(rows)
            total_affected += affected
            print(
                f"scraper complete: {config['name']} items={len(rows)} "
                f"affected={affected} duration_ms={duration_ms}"
            )

            _update_log_safe(
                client,
                log_id,
                {
                    "status": "success",
                    "duration_ms": duration_ms,
                    "result": {
                        **run_context,
                        "items_count": len(rows),
                        "raw_items_affected": affected,
                        "write_rds": bool(args.write_rds),
                        "output_path": str(output_path) if output_path else None,
                    },
                    "error_message": None,
                    "error_logs": [],
                },
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            failed += 1
            print(f"scraper failed: {config['name']} [{config['type']}] {exc}")
            _update_log_safe(
                client,
                log_id,
                {
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "result": {
                        **run_context,
                        "items_count": 0,
                        "raw_items_affected": 0,
                        "write_rds": bool(args.write_rds),
                    },
                    "error_message": str(exc),
                    "error_logs": [
                        {
                            "type": exc.__class__.__name__,
                            "message": str(exc),
                        }
                    ],
                },
            )
            if not args.continue_on_error:
                raise

    print(
        "global scrape summary: "
        f"configs={len(configs)} skipped={len(skipped)} failed={failed} "
        f"items={total_items} affected={total_affected}"
    )
    return 1 if failed and args.fail_on_error else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run all enabled Octopus scraper configs")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Snapshot date, e.g. 2026-06-07",
    )
    parser.add_argument(
        "--output",
        default="outputs/global_scrape/raw_items.jsonl",
        help="Optional JSONL output path for fetched raw_items rows.",
    )
    parser.add_argument(
        "--write-rds",
        action="store_true",
        help="Write crawler output to Aliyun RDS MySQL using OCTOPUS_RDS_* env vars.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue running remaining configs after a scraper failure.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit non-zero after all configs finish if any scraper failed.",
    )
    return parser


def main() -> None:
    load_dotenv()
    raise SystemExit(run_global_scrape(build_parser().parse_args()))


if __name__ == "__main__":
    main()
