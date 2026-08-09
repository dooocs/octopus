from __future__ import annotations

import argparse
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:
        return None

from crawler.core.registry import list_types
from crawler.core.runner import run_config
from crawler.outputs import JsonlOutput, RdsOutput
from infra.supabase import (
    SupabaseRestClient,
    load_enabled_runtime_configs,
    split_supported_configs,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _github_run_context() -> dict[str, Any]:
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "")
    run_url = f"{server_url}/{repository}/actions/runs/{run_id}" if repository and run_id else ""
    return {
        "github_run_id": run_id or None,
        "github_run_attempt": run_attempt or None,
        "github_run_url": run_url or None,
        "github_workflow": os.getenv("GITHUB_WORKFLOW") or None,
        "github_ref": os.getenv("GITHUB_REF") or None,
        "github_sha": os.getenv("GITHUB_SHA") or None,
        "github_actor": os.getenv("GITHUB_ACTOR") or None,
    }


def _config_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": config.get("id"),
        "name": config.get("name"),
        "scraper": config.get("scraper"),
        "enabled": config.get("enabled"),
        "priority": config.get("priority"),
        "source_type": config.get("source_type"),
        "sub_source_type": config.get("sub_source_type"),
        "item_type": config.get("item_type"),
        "input_schema_version": config.get("input_schema_version"),
        "input": config.get("input") or {},
    }


def _update_task_safe(client: SupabaseRestClient, task_id: str | None, payload: dict[str, Any]) -> None:
    if not task_id:
        return
    try:
        client.update_scrape_task(task_id, payload)
    except Exception as exc:
        print(f"  ⚠️ update scrape task failed: {exc}")


def _update_run_safe(client: SupabaseRestClient, run_id: str | None, payload: dict[str, Any]) -> None:
    if not run_id:
        return
    try:
        client.update_scrape_run(run_id, payload)
    except Exception as exc:
        print(f"  ⚠️ update scrape run failed: {exc}")


def run_global_scrape(args: argparse.Namespace) -> int:
    client = SupabaseRestClient.from_env()
    run_context = _github_run_context()
    configs, skipped = split_supported_configs(load_enabled_runtime_configs(client), list_types())

    print(f"loaded enabled configs: runnable={len(configs)}, skipped={len(skipped)}")
    if skipped:
        skipped_names = ", ".join(f"{c['name']}({c['scraper']})" for c in skipped)
        print(f"skipped unsupported scraper configs: {skipped_names}")

    jsonl_output = JsonlOutput(Path(args.output)) if args.output else None
    if jsonl_output:
        jsonl_output.reset()
    rds_output = RdsOutput() if args.write_rds else None

    run_id = client.create_scrape_run(
        {
            "snapshot_date": args.date,
            "trigger_type": "github_action" if run_context.get("github_run_id") else "manual",
            "trigger_ref": run_context.get("github_run_url"),
            "status": "running",
            "summary": {**run_context, "skipped_unsupported": len(skipped)},
        }
    )

    total_items = 0
    total_written = 0
    failed = 0

    for config in configs:
        print(f"running scraper: {config['name']} [{config['scraper']}]")
        started = time.monotonic()
        task_id = client.create_scrape_task(
            {
                "run_id": run_id,
                "scraper_config_id": config.get("id"),
                "snapshot_date": args.date,
                "scraper": config.get("scraper"),
                "sub_source_type": config.get("sub_source_type"),
                "status": "running",
                "stage": "validate_config",
                "config_snapshot": _config_snapshot(config),
            }
        )

        try:
            state = client.fetch_scraper_state(str(config.get("id"))) if config.get("id") else {}
            result = run_config(
                config,
                args.date,
                run_id=run_id,
                task_id=task_id,
                state=state,
                on_stage=lambda stage: _update_task_safe(client, task_id, {"stage": stage}),
            )

            _update_task_safe(
                client,
                task_id,
                {
                    "stage": "sink",
                    "items_discovered": result.items_discovered,
                    "items_filtered": result.items_filtered,
                    "items_enriched": result.items_enriched,
                },
            )

            written = 0
            if jsonl_output:
                jsonl_output.write(result.rows)
            if rds_output:
                written = rds_output.write(result.rows)

            duration_ms = int((time.monotonic() - started) * 1000)
            total_items += len(result.rows)
            total_written += written

            if config.get("id"):
                client.upsert_scraper_state(
                    {
                        "scraper_config_id": config.get("id"),
                        "state": result.state or {},
                        "last_success_snapshot_date": args.date,
                        "last_success_run_id": run_id,
                    }
                )

            _update_task_safe(
                client,
                task_id,
                {
                    "status": "success",
                    "stage": "done",
                    "items_written": written,
                    "duration_ms": duration_ms,
                    "error_message": None,
                    "error_logs": [],
                },
            )
            print(
                f"scraper complete: {config['name']} items={len(result.rows)} "
                f"written={written} duration_ms={duration_ms}"
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            failed += 1
            print(f"scraper failed: {config['name']} [{config['scraper']}] {exc}")
            _update_task_safe(
                client,
                task_id,
                {
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "error_message": str(exc),
                    "error_logs": [{"type": exc.__class__.__name__, "message": str(exc)}],
                },
            )
            if args.fail_fast or not args.continue_on_error:
                _update_run_safe(
                    client,
                    run_id,
                    {
                        "status": "failed",
                        "finished_at": _utc_now_iso(),
                        "summary": {
                            **run_context,
                            "configs": len(configs),
                            "skipped": len(skipped),
                            "failed": failed,
                            "items": total_items,
                            "written": total_written,
                        },
                    },
                )
                raise

    status = "success" if failed == 0 else "partial"
    summary = {
        **run_context,
        "configs": len(configs),
        "skipped": len(skipped),
        "failed": failed,
        "items": total_items,
        "written": total_written,
        "write_rds": bool(args.write_rds),
        "output_path": str(args.output) if args.output else None,
    }
    _update_run_safe(
        client,
        run_id,
        {
            "status": status,
            "finished_at": _utc_now_iso(),
            "summary": summary,
        },
    )
    print(
        "global scrape summary: "
        f"configs={len(configs)} skipped={len(skipped)} failed={failed} "
        f"items={total_items} written={total_written}"
    )
    return 1 if failed and args.fail_on_error else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run all enabled Octopus scraper configs")
    parser.add_argument("--date", default=date.today().isoformat(), help="Snapshot date, e.g. 2026-06-07")
    parser.add_argument(
        "--output",
        default="outputs/global_scrape/raw_items.jsonl",
        help="Optional JSONL output path for fetched raw_items rows.",
    )
    parser.add_argument("--write-rds", action="store_true", help="Write crawler output to Aliyun RDS MySQL.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue after scraper failure.")
    parser.add_argument("--fail-on-error", action="store_true", help="Exit non-zero after all configs finish if failed.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop immediately on first scraper failure.")
    return parser


def main() -> None:
    load_dotenv()
    raise SystemExit(run_global_scrape(build_parser().parse_args()))


if __name__ == "__main__":
    main()
