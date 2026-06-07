from __future__ import annotations

from typing import Any

from scrapers.registry import get_engine, list_types


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
