from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping


def _parse_datetime(value: Any, *, field_name: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    else:
        raise TypeError(f"{field_name} must be datetime, ISO string, or None")

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_required_datetime(value: Any, *, field_name: str) -> datetime:
    dt = _parse_datetime(value, field_name=field_name)
    if dt is None:
        raise ValueError(f"{field_name} is required")
    return dt


def _parse_date(value: Any, *, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"{field_name} must be date or ISO date string")


def _dict_or_none(value: Any, *, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    raise TypeError(f"{field_name} must be a dict or None")


def _required_string(row: Mapping[str, Any], field_name: str) -> str:
    value = row.get(field_name)
    if value is None or value == "":
        raise ValueError(f"{field_name} is required")
    return str(value)


@dataclass(frozen=True)
class RawItemRecord:
    """Final raw_items table model for MySQL/RDS writes."""

    id: str
    url: str
    source_type: str
    sub_source_type: str
    item_type: str
    author_id: str | None
    author_url: str | None
    created_date: datetime
    updated_date: datetime
    source_published_date: datetime | None
    snapshot_date: date
    title: str
    metrics: dict[str, Any] | None
    content: str | None
    context_content: dict[str, Any] | None
    extra: dict[str, Any] | None
    scrape_config_snapshot: dict[str, Any] | None

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> RawItemRecord:
        return cls(
            id=_required_string(row, "id"),
            url=_required_string(row, "url"),
            source_type=_required_string(row, "source_type"),
            sub_source_type=_required_string(row, "sub_source_type"),
            item_type=_required_string(row, "item_type"),
            author_id=row.get("author_id") or None,
            author_url=row.get("author_url") or None,
            created_date=_parse_required_datetime(row.get("created_date"), field_name="created_date"),
            updated_date=_parse_required_datetime(row.get("updated_date"), field_name="updated_date"),
            source_published_date=_parse_datetime(
                row.get("source_published_date"),
                field_name="source_published_date",
            ),
            snapshot_date=_parse_date(row.get("snapshot_date"), field_name="snapshot_date"),
            title=_required_string(row, "title"),
            metrics=_dict_or_none(row.get("metrics"), field_name="metrics"),
            content=row.get("content") or None,
            context_content=_dict_or_none(row.get("context_content"), field_name="context_content"),
            extra=_dict_or_none(row.get("extra"), field_name="extra"),
            scrape_config_snapshot=_dict_or_none(
                row.get("scrape_config_snapshot"),
                field_name="scrape_config_snapshot",
            ),
        )

    @classmethod
    def from_crawler_item(cls, item: Any) -> RawItemRecord:
        if not hasattr(item, "to_output_dict"):
            raise TypeError("crawler item must expose to_output_dict()")
        return cls.from_mapping(item.to_output_dict())
