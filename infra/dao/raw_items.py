from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .base import AliyunRdsDaoBase
from .models import RawItemRecord


def _json_param(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RawItemsDao:
    """Write operations for the final raw_items table."""

    COLUMNS = (
        "id",
        "url",
        "source_type",
        "sub_source_type",
        "item_type",
        "author_id",
        "author_url",
        "created_date",
        "updated_date",
        "source_published_date",
        "snapshot_date",
        "title",
        "metrics",
        "content",
        "context_content",
        "extra",
        "scrape_config_snapshot",
    )

    UPDATE_COLUMNS = tuple(col for col in COLUMNS if col not in {"id", "created_date"})

    def __init__(self, db: AliyunRdsDaoBase, table_name: str = "raw_items"):
        self.db = db
        self.table_name = table_name

    @property
    def upsert_sql(self) -> str:
        columns = ", ".join(f"`{col}`" for col in self.COLUMNS)
        placeholders = ", ".join(["%s"] * len(self.COLUMNS))
        updates = ", ".join(f"`{col}` = VALUES(`{col}`)" for col in self.UPDATE_COLUMNS)
        return (
            f"INSERT INTO `{self.table_name}` ({columns}) "
            f"VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {updates}"
        )

    def upsert(self, record: RawItemRecord) -> int:
        return self.db.execute_update(self.upsert_sql, self._params(record))

    def upsert_many(self, records: Iterable[RawItemRecord]) -> int:
        return self.db.executemany_update(self.upsert_sql, (self._params(record) for record in records))

    def upsert_from_mapping(self, row: dict[str, Any]) -> int:
        return self.upsert(RawItemRecord.from_mapping(row))

    def upsert_many_from_mappings(self, rows: Iterable[dict[str, Any]]) -> int:
        return self.upsert_many(RawItemRecord.from_mapping(row) for row in rows)

    def upsert_from_crawler_item(self, item: Any) -> int:
        return self.upsert(RawItemRecord.from_crawler_item(item))

    def upsert_many_from_crawler_items(self, items: Iterable[Any]) -> int:
        return self.upsert_many(RawItemRecord.from_crawler_item(item) for item in items)

    def select_published_since(self, sub_source_types: Sequence[str], since: datetime) -> list[dict[str, Any]]:
        """Rows of these sub source types published at or after `since` (UTC), oldest first."""
        if not sub_source_types:
            return []
        placeholders = ", ".join(["%s"] * len(sub_source_types))
        sql = (
            f"SELECT * FROM `{self.table_name}` "
            f"WHERE `sub_source_type` IN ({placeholders}) AND `source_published_date` >= %s "
            "ORDER BY `source_published_date` ASC"
        )
        params = [*sub_source_types, since.astimezone(timezone.utc).replace(tzinfo=None)]
        return [_decode_row(row) for row in self.db.execute_query(sql, params)]

    def select_by_ids(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ", ".join(["%s"] * len(ids))
        sql = f"SELECT * FROM `{self.table_name}` WHERE `id` IN ({placeholders})"
        return [_decode_row(row) for row in self.db.execute_query(sql, list(ids))]

    def update_context_and_extra(self, row_id: str, context_content: dict[str, Any], extra: dict[str, Any]) -> int:
        """Rewrites only the two JSON columns of one row (maintenance scripts)."""
        sql = f"UPDATE `{self.table_name}` SET `context_content` = %s, `extra` = %s WHERE `id` = %s"
        return self.db.execute_update(sql, (_json_param(context_content), _json_param(extra), row_id))

    def _params(self, record: RawItemRecord) -> Sequence[Any]:
        return (
            record.id,
            record.url,
            record.source_type,
            record.sub_source_type,
            record.item_type,
            record.author_id,
            record.author_url,
            record.created_date,
            record.updated_date,
            record.source_published_date,
            record.snapshot_date,
            record.title,
            _json_param(record.metrics),
            record.content,
            _json_param(record.context_content),
            _json_param(record.extra),
            _json_param(record.scrape_config_snapshot),
        )


_JSON_COLUMNS = ("metrics", "context_content", "extra", "scrape_config_snapshot")


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    """JSON columns as objects, DATETIME columns as aware UTC datetimes."""
    out = dict(row)
    for key in _JSON_COLUMNS:
        value = out.get(key)
        if isinstance(value, (str, bytes)):
            out[key] = json.loads(value) if value else None
    for key in ("created_date", "updated_date", "source_published_date"):
        value = out.get(key)
        if isinstance(value, datetime) and value.tzinfo is None:
            out[key] = value.replace(tzinfo=timezone.utc)
    return out
