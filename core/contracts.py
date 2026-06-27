from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping, Protocol, runtime_checkable


INPUT_KEYS = ("source", "fetch", "filters", "enrich")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def raw_item_id_from_url(original_url: str) -> str:
    return hashlib.md5(original_url.encode()).hexdigest()


def _as_iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"{field_name} must be a JSON object")


def _json_array(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    raise TypeError(f"{field_name} must be a JSON array")


@dataclass(frozen=True)
class InputConfig:
    source: dict[str, Any] = field(default_factory=dict)
    fetch: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    enrich: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "InputConfig":
        keys = set(value.keys())
        expected = set(INPUT_KEYS)
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"missing={','.join(missing)}")
            if extra:
                parts.append(f"extra={','.join(extra)}")
            raise ValueError(f"input must contain exactly {','.join(INPUT_KEYS)} ({'; '.join(parts)})")
        enrich = _json_array(value.get("enrich"), field_name="input.enrich")
        normalized_enrich: list[dict[str, Any]] = []
        for index, item in enumerate(enrich):
            if not isinstance(item, dict):
                raise TypeError(f"input.enrich[{index}] must be a JSON object")
            normalized_enrich.append(dict(item))
        return cls(
            source=_json_object(value.get("source"), field_name="input.source"),
            fetch=_json_object(value.get("fetch"), field_name="input.fetch"),
            filters=_json_object(value.get("filters"), field_name="input.filters"),
            enrich=normalized_enrich,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": dict(self.source),
            "fetch": dict(self.fetch),
            "filters": dict(self.filters),
            "enrich": [dict(item) for item in self.enrich],
        }


@dataclass(frozen=True)
class ScraperConfig:
    id: str | None
    scraper: str
    name: str
    enabled: bool
    priority: int
    source_type: str
    sub_source_type: str
    item_type: str
    input: InputConfig
    input_schema_version: int = 1

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ScraperConfig":
        scraper = row.get("scraper")
        raw_input = row.get("input")
        required = {
            "scraper": scraper,
            "name": row.get("name"),
            "source_type": row.get("source_type"),
            "sub_source_type": row.get("sub_source_type"),
            "item_type": row.get("item_type"),
            "input": raw_input,
        }
        missing = [key for key, value in required.items() if value in (None, "")]
        if missing:
            raise ValueError(f"scraper config is missing required fields: {', '.join(missing)}")
        if not isinstance(raw_input, Mapping):
            raise TypeError("scraper config input must be a JSON object")
        return cls(
            id=str(row["id"]) if row.get("id") else None,
            scraper=str(scraper),
            name=str(row.get("name")),
            enabled=bool(row.get("enabled", True)),
            priority=int(row.get("priority") or 100),
            source_type=str(row.get("source_type")),
            sub_source_type=str(row.get("sub_source_type")),
            item_type=str(row.get("item_type")),
            input=InputConfig.from_mapping(raw_input),
            input_schema_version=int(row.get("input_schema_version") or 1),
        )

    def to_runtime_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scraper": self.scraper,
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
            "source_type": self.source_type,
            "sub_source_type": self.sub_source_type,
            "item_type": self.item_type,
            "input_schema_version": self.input_schema_version,
            "input": self.input.to_dict(),
        }


@dataclass(frozen=True)
class RunContext:
    snapshot_date: str
    run_id: str | None = None
    task_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceRecord:
    identity: str
    url: str
    title: str
    raw: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    context_content: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    author_id: str = ""
    author_url: str = ""
    source_published_date: datetime | None = None


@dataclass(init=False)
class RawItem:
    title: str
    original_url: str
    source_name: str
    source_type: str
    item_type_value: str
    identity: str
    author: str
    author_url: str
    body_text: str
    raw_metrics: dict[str, Any]
    extra: dict[str, Any]
    published_at: datetime | None
    snapshot_date: date | str | None
    scraper_slug: str
    scraper_config_snapshot: dict[str, Any]
    context_content: dict[str, Any]

    def __init__(
        self,
        *,
        title: str,
        original_url: str,
        source_name: str = "",
        source_type: str,
        item_type: str | None = None,
        identity: str | None = None,
        author: str = "",
        author_url: str = "",
        body_text: str = "",
        raw_metrics: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        published_at: datetime | None = None,
        snapshot_date: date | str | None = None,
        scraper_slug: str = "",
        scraper_config_snapshot: dict[str, Any] | None = None,
        context_content: dict[str, Any] | None = None,
    ) -> None:
        if not item_type:
            raise ValueError("item_type is required")
        self.title = title
        self.original_url = original_url
        self.source_name = source_name
        self.source_type = source_type
        self.item_type_value = item_type
        self.identity = identity or original_url
        self.author = author
        self.author_url = author_url
        self.body_text = body_text
        self.raw_metrics = raw_metrics or {}
        self.extra = extra or {}
        self.published_at = published_at
        self.snapshot_date = snapshot_date
        self.scraper_slug = scraper_slug
        self.scraper_config_snapshot = scraper_config_snapshot or {}
        self.context_content = context_content or {}

    @property
    def url(self) -> str:
        return self.original_url

    @property
    def item_type(self) -> str:
        return self.item_type_value

    @item_type.setter
    def item_type(self, value: str) -> None:
        self.item_type_value = value

    @property
    def sub_source_type(self) -> str:
        return self.scraper_slug or self.extra.get("source_tag") or self.source_name

    @property
    def id(self) -> str:
        return raw_item_id_from_url(self.original_url)

    def to_output_dict(self) -> dict[str, Any]:
        now = _utc_now_iso()
        snapshot = _as_iso(self.snapshot_date)
        if snapshot is None:
            snapshot = datetime.now(timezone.utc).date().isoformat()

        extra = dict(self.extra or {})
        context = dict(self.context_content or {})
        embedded_context = extra.pop("context_content", None)
        if isinstance(embedded_context, dict):
            context.update(embedded_context)
        if self.identity and self.identity != self.original_url:
            extra.setdefault("native_id", self.identity)

        return {
            "id": self.id,
            "url": self.original_url,
            "source_type": self.source_type,
            "sub_source_type": self.sub_source_type,
            "item_type": self.item_type,
            "author_id": self.author or None,
            "author_url": self.author_url or None,
            "created_date": now,
            "updated_date": now,
            "source_published_date": _as_iso(self.published_at),
            "snapshot_date": snapshot,
            "title": self.title,
            "metrics": self.raw_metrics or None,
            "content": self.body_text or None,
            "context_content": context or None,
            "extra": extra or None,
            "scrape_config_snapshot": self.scraper_config_snapshot or None,
        }

    def to_db_dict(self) -> dict[str, Any]:
        return self.to_output_dict()


@dataclass(frozen=True)
class ChannelSpec:
    scraper: str
    label: str
    group: str
    default_source_type: str
    default_item_type: str
    input_schema_version: int
    input_schema: dict[str, Any]
    default_input: dict[str, Any]
    required_secrets: list[str] = field(default_factory=list)
    supported_enrichers: list[str] = field(default_factory=list)
    rate_limit: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "scraper": self.scraper,
            "label": self.label,
            "group": self.group,
            "default_source_type": self.default_source_type,
            "default_item_type": self.default_item_type,
            "input_schema_version": self.input_schema_version,
            "input_schema": self.input_schema,
            "default_input": self.default_input,
            "required_secrets": self.required_secrets,
            "supported_enrichers": self.supported_enrichers,
            "rate_limit": self.rate_limit,
            "description": self.description,
        }


@dataclass(frozen=True)
class ScrapeTaskResult:
    rows: list[dict[str, Any]]
    items_discovered: int
    items_filtered: int
    items_enriched: int
    items_written: int = 0
    state: dict[str, Any] = field(default_factory=dict)


class SourceAdapterBase:
    spec: ChannelSpec

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        return records

    def prune(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        return records


@runtime_checkable
class SourceAdapter(Protocol):
    spec: ChannelSpec

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        ...

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        ...

    def prune(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        ...

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        ...


def enrich_enabled(config: ScraperConfig, name: str) -> bool:
    return any(item.get("name") == name for item in config.input.enrich if isinstance(item, dict))
