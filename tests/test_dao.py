from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

from infra.dao import RawItemRecord, RawItemsDao
from infra.models import RawItem


class FakeDb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Sequence[Any]]]] = []

    def execute_update(self, sql: str, params: Sequence[Any]) -> int:
        self.calls.append((sql, [params]))
        return 1

    def executemany_update(self, sql: str, params_seq: Iterable[Sequence[Any]]) -> int:
        params = list(params_seq)
        self.calls.append((sql, params))
        return len(params)


class RawItemsDaoTest(unittest.TestCase):
    def test_record_from_crawler_item_normalizes_contract_fields(self) -> None:
        item = RawItem(
            title="Demo",
            original_url="https://example.com/demo",
            source_name="Example",
            source_type="website",
            content_type="article",
            body_text="Main body",
            raw_metrics={"views": 10},
            extra={"context_content": {"summary": "Short"}, "source_tag": "example"},
            published_at=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
            snapshot_date="2026-06-06",
            scraper_slug="example_rss",
        )

        record = RawItemRecord.from_crawler_item(item)

        self.assertEqual(record.url, "https://example.com/demo")
        self.assertEqual(record.sub_source_type, "example_rss")
        self.assertEqual(record.item_type, "article")
        self.assertEqual(record.content, "Main body")
        self.assertEqual(record.context_content, {"summary": "Short"})
        self.assertEqual(record.metrics, {"views": 10})
        self.assertEqual(record.source_published_date, datetime(2026, 6, 6, 8))
        self.assertEqual(record.snapshot_date, date(2026, 6, 6))

    def test_raw_items_upsert_serializes_json_params(self) -> None:
        db = FakeDb()
        dao = RawItemsDao(db)  # type: ignore[arg-type]
        record = RawItemRecord(
            id="abc",
            url="https://example.com/demo",
            source_type="website",
            sub_source_type="example",
            item_type="article",
            author_id=None,
            author_url=None,
            created_date=datetime(2026, 6, 6, 1),
            updated_date=datetime(2026, 6, 6, 2),
            source_published_date=None,
            snapshot_date=date(2026, 6, 6),
            title="Demo",
            metrics={"views": 10},
            content="Main body",
            context_content={"summary": "Short"},
            extra={"feed_url": "https://example.com/feed"},
            scrape_config_snapshot={"max_items": 10},
        )

        affected = dao.upsert(record)

        self.assertEqual(affected, 1)
        sql, params_seq = db.calls[0]
        params = params_seq[0]
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)
        self.assertNotIn("`created_date` = VALUES(`created_date`)", sql)
        self.assertEqual(json.loads(params[12]), {"views": 10})
        self.assertEqual(json.loads(params[14]), {"summary": "Short"})
        self.assertEqual(json.loads(params[15]), {"feed_url": "https://example.com/feed"})
        self.assertEqual(json.loads(params[16]), {"max_items": 10})


if __name__ == "__main__":
    unittest.main()
