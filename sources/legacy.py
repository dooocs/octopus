from __future__ import annotations

import importlib
from typing import Any

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceRecord


def input_schema(
    *,
    source: dict[str, Any] | None = None,
    fetch: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    enrich_names: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["source", "fetch", "filters", "enrich", "runtime"],
        "additionalProperties": False,
        "properties": {
            "source": {"type": "object", "additionalProperties": False, "properties": source or {}},
            "fetch": {"type": "object", "additionalProperties": False, "properties": fetch or {}},
            "filters": {"type": "object", "additionalProperties": False, "properties": filters or {}},
            "enrich": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "enum": enrich_names or []},
                        "when": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
            "runtime": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "timeout": {"type": "number", "minimum": 1},
                    "retries": {"type": "integer", "minimum": 0},
                    "concurrency": {"type": "integer", "minimum": 1},
                    "rate_limit": {"type": "object"},
                },
            },
        },
    }


def default_input(
    *,
    source: dict[str, Any] | None = None,
    fetch: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    enrich: list[dict[str, Any]] | None = None,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source": source or {},
        "fetch": fetch or {},
        "filters": filters or {},
        "enrich": enrich or [],
        "runtime": runtime or {},
    }


def flat_config(config: ScraperConfig) -> dict[str, Any]:
    value = config.input.to_dict()
    merged: dict[str, Any] = {
        "source_type": config.source_type,
        "content_type": config.item_type,
    }
    for section in ("source", "fetch", "filters", "runtime"):
        merged.update(value.get(section) or {})
    merged["enrich"] = value.get("enrich") or []
    return merged


class LegacyEngineAdapter:
    spec: ChannelSpec
    engine_cls: type | None = None
    engine_path: str = ""

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        engine = self._engine(config, ctx)
        discover_items = getattr(engine, "discover_items", None)
        items = discover_items() if callable(discover_items) else engine.fetch()
        return [_record_from_item(item) for item in items]

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        engine = self._engine(config, ctx)
        enrich_items = getattr(engine, "enrich_items", None)
        if not callable(enrich_items):
            return records
        items = [record.raw["item"] for record in records if "item" in record.raw]
        enriched_items = enrich_items(items)
        return [_record_from_item(item) for item in enriched_items]

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        item = record.raw["item"]
        item.identity = record.identity
        item.source_type = config.source_type
        item.content_type = config.item_type
        item.scraper_slug = config.sub_source_type
        return item

    def _engine(self, config: ScraperConfig, ctx: RunContext) -> Any:
        engine_cls = self._engine_cls()
        engine = engine_cls(name=config.name, config=flat_config(config))
        engine.snapshot_date = ctx.snapshot_date
        return engine

    @classmethod
    def _engine_cls(cls) -> type:
        if cls.engine_cls is not None:
            return cls.engine_cls
        if not cls.engine_path:
            raise ValueError(f"{cls.__name__} must define engine_cls or engine_path")
        module_name, class_name = cls.engine_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        engine_cls = getattr(module, class_name)
        cls.engine_cls = engine_cls
        return engine_cls


def _record_from_item(item: Any) -> SourceRecord:
    return SourceRecord(
        identity=_identity_for_item(item),
        url=item.original_url,
        title=item.title,
        raw={"item": item},
        metrics=dict(item.raw_metrics or {}),
        content=item.body_text or "",
        context_content=dict(item.context_content or {}),
        extra=dict(item.extra or {}),
        author_id=item.author or "",
        author_url=item.author_url or "",
        source_published_date=item.published_at,
    )


def _identity_for_item(item: Any) -> str:
    extra = item.extra or {}
    metrics = item.raw_metrics or {}
    for key in (
        "native_id",
        "repo_id",
        "tweet_id",
        "post_id",
        "topic_id",
        "model_id",
        "paper_id",
        "ph_id",
        "arxiv_id",
    ):
        value = extra.get(key) or metrics.get(key)
        if value not in (None, ""):
            return str(value)
    return item.original_url


STRING = {"type": "string"}
NUMBER = {"type": "number"}
INTEGER = {"type": "integer"}
BOOLEAN = {"type": "boolean"}
STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
JSON_OBJECT = {"type": "object", "additionalProperties": True}
