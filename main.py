from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from infra.dao import OctopusDao, RawItemRecord
from scrapers.registry import get_engine, list_types


def _load_configs(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("scraper config file must contain a JSON array")
    return data


def _slug(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )


def run_scrapers(configs: list[dict[str, Any]], snapshot_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for sc in configs:
        if sc.get("enabled") is False:
            continue

        scraper_type = sc["type"]
        engine_cls = get_engine(scraper_type)
        if engine_cls is None:
            raise ValueError(f"unknown scraper type: {scraper_type}; available={list_types()}")

        name = sc.get("name") or scraper_type
        config = dict(sc.get("config") or {})
        config.setdefault("source_type", sc.get("source_type", "website"))
        config.setdefault("content_type", sc.get("item_type", "article"))

        engine = engine_cls(name=name, config=config)
        engine.snapshot_date = snapshot_date
        items = engine.fetch()

        sub_source_type = sc.get("sub_source_type") or config.get("source_tag") or _slug(name)
        for item in items:
            item.snapshot_date = snapshot_date
            item.scraper_slug = sub_source_type
            item.source_type = config.get("source_type", item.source_type)
            item.content_type = config.get("content_type", item.content_type)
            item.scraper_config_snapshot = config
            rows.append(item.to_output_dict())

    return rows


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run Octopus crawler engines")
    parser.add_argument(
        "--config",
        default="configs/scrapers.example.json",
        help="Path to scraper config JSON array",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Snapshot date, e.g. 2026-06-06",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSONL output path. Defaults to stdout.",
    )
    parser.add_argument(
        "--write-rds",
        action="store_true",
        help="Write crawler output to Aliyun RDS MySQL using OCTOPUS_RDS_* env vars.",
    )
    args = parser.parse_args()

    rows = run_scrapers(_load_configs(Path(args.config)), args.date)

    if args.write_rds:
        records = [RawItemRecord.from_mapping(row) for row in rows]
        with OctopusDao.from_env() as dao:
            dao.raw_items.upsert_many(records)

    lines = [json.dumps(row, ensure_ascii=False, default=str) for row in rows]
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    else:
        for line in lines:
            print(line)


if __name__ == "__main__":
    main()
