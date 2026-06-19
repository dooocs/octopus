from __future__ import annotations

from typing import Any

from .contracts import RunContext, ScrapeTaskResult, ScraperConfig
from .registry import get_adapter, list_types
from .validation import validate_config, validate_raw_item


def run_config(
    config_row: dict[str, Any],
    snapshot_date: str,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    state: dict[str, Any] | None = None,
) -> ScrapeTaskResult:
    config = ScraperConfig.from_mapping(config_row)
    adapter_cls = get_adapter(config.scraper)
    if adapter_cls is None:
        raise ValueError(f"unknown scraper type: {config.scraper}; available={list_types()}")
    validate_config(adapter_cls.spec, config)

    adapter = adapter_cls()
    ctx = RunContext(snapshot_date=snapshot_date, run_id=run_id, task_id=task_id, state=state or {})
    records = adapter.discover(ctx, config)
    filtered = records
    enriched = adapter.enrich(ctx, filtered, config)

    rows: list[dict[str, Any]] = []
    for record in enriched:
        item = adapter.normalize(ctx, record, config)
        item.snapshot_date = snapshot_date
        item.scraper_slug = config.sub_source_type
        item.source_type = config.source_type
        item.content_type = config.item_type
        item.scraper_config_snapshot = {
            "id": config.id,
            "name": config.name,
            "scraper": config.scraper,
            "source_type": config.source_type,
            "sub_source_type": config.sub_source_type,
            "item_type": config.item_type,
            "input_schema_version": config.input_schema_version,
            "input": config.input.to_dict(),
        }
        validate_raw_item(item)
        rows.append(item.to_output_dict())

    return ScrapeTaskResult(
        rows=rows,
        items_discovered=len(records),
        items_filtered=len(filtered),
        items_enriched=len(enriched),
        state=ctx.state,
    )


def run_scrapers(configs: list[dict[str, Any]], snapshot_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for config in configs:
        if config.get("enabled") is False:
            continue
        rows.extend(run_config(config, snapshot_date).rows)
    return rows
