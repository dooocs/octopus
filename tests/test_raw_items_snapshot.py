from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch

from scripts.sync_raw_items_snapshot import (
    SNAPSHOT_TABLE,
    SupabaseSnapshotClient,
    normalize_raw_item_row,
    sync_raw_items_snapshot,
)


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class RawItemsSnapshotSyncTest(unittest.TestCase):
    def test_normalize_raw_item_row_converts_mysql_values_for_supabase(self) -> None:
        row = {
            "id": "0" * 32,
            "url": "https://example.com/item",
            "source_type": "website",
            "sub_source_type": "example",
            "item_type": "article",
            "author_id": None,
            "author_url": None,
            "created_date": datetime(2026, 6, 13, 1, 2, 3, 123000),
            "updated_date": datetime(2026, 6, 13, 2, 3, 4, 456000),
            "source_published_date": None,
            "snapshot_date": date(2026, 6, 13),
            "title": "Demo",
            "metrics": '{"views":10}',
            "content": "Body",
            "context_content": '{"summary":"Short"}',
            "extra": None,
            "scrape_config_snapshot": '{"max_items":5}',
        }

        normalized = normalize_raw_item_row(row)

        self.assertEqual(normalized["created_date"], "2026-06-13T01:02:03.123000")
        self.assertEqual(normalized["snapshot_date"], "2026-06-13")
        self.assertEqual(normalized["metrics"], {"views": 10})
        self.assertEqual(normalized["context_content"], {"summary": "Short"})
        self.assertIsNone(normalized["extra"])
        self.assertEqual(normalized["scrape_config_snapshot"], {"max_items": 5})

    @patch("scripts.sync_raw_items_snapshot.requests.post")
    @patch("scripts.sync_raw_items_snapshot.requests.delete")
    def test_replace_rows_deletes_then_inserts_chunks(
        self,
        mock_delete: Mock,
        mock_post: Mock,
    ) -> None:
        mock_delete.return_value = FakeResponse()
        mock_post.return_value = FakeResponse()
        client = SupabaseSnapshotClient("https://example.supabase.co", "service-key")

        inserted = client.replace_rows(
            [{"id": "1"}, {"id": "2"}, {"id": "3"}],
            chunk_size=2,
        )

        self.assertEqual(inserted, 3)
        mock_delete.assert_called_once()
        self.assertIn(SNAPSHOT_TABLE, mock_delete.call_args.args[0])
        self.assertEqual(mock_delete.call_args.kwargs["params"], {"id": "not.is.null"})
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_post.call_args_list[0].kwargs["json"], [{"id": "1"}, {"id": "2"}])
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"], [{"id": "3"}])

    @patch("scripts.sync_raw_items_snapshot.iter_rds_raw_items")
    def test_sync_raw_items_snapshot_filters_by_snapshot_date(self, mock_iter: Mock) -> None:
        client = Mock()
        client.replace_rows.return_value = 2
        mock_iter.return_value = iter([{"id": "1"}, {"id": "2"}])

        inserted = sync_raw_items_snapshot(
            snapshot_date="2026-06-13",
            batch_size=500,
            chunk_size=300,
            client=client,
        )

        self.assertEqual(inserted, 2)
        mock_iter.assert_called_once_with("2026-06-13", batch_size=500)
        client.replace_rows.assert_called_once()
        self.assertEqual(client.replace_rows.call_args.kwargs["chunk_size"], 300)


if __name__ == "__main__":
    unittest.main()
