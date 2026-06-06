from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


@dataclass
class RawItem:
    """Crawler output item.

    The fields keep compatibility with the existing AmazingIndex scrapers while
    `to_output_dict()` emits the Octopus final raw_items contract.
    """

    title: str
    original_url: str
    source_name: str
    source_type: str
    content_type: str
    author: str = ""
    author_url: str = ""
    body_text: str = ""
    raw_metrics: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    published_at: datetime | None = None
    snapshot_date: date | str | None = None
    scraper_slug: str = ""
    scraper_config_snapshot: dict[str, Any] = field(default_factory=dict)
    context_content: dict[str, Any] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return self.original_url

    @property
    def item_type(self) -> str:
        return self.content_type

    @property
    def sub_source_type(self) -> str:
        return self.scraper_slug or self.extra.get("source_tag") or self.source_name

    @property
    def id(self) -> str:
        identity = f"{self.source_type}:{self.sub_source_type}:{self.original_url}"
        return hashlib.md5(identity.encode()).hexdigest()

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
        """Backward-compatible alias used by existing scraper tests/tools."""
        return self.to_output_dict()


@dataclass
class BaseScraper:
    """Base class for configured crawler engines."""

    name: str
    config: dict[str, Any]
    snapshot_date: str | None = None

    def fetch(self) -> list[RawItem]:
        raise NotImplementedError
