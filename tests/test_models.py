from __future__ import annotations

import unittest
from datetime import datetime, timezone

from infra.models import RawItem


class RawItemOutputContractTest(unittest.TestCase):
    def test_to_output_dict_maps_legacy_scraper_fields(self) -> None:
        item = RawItem(
            title="Demo",
            original_url="https://example.com/demo",
            source_name="Example RSS",
            source_type="website",
            content_type="article",
            author="alice",
            author_url="https://example.com/alice",
            body_text="Main body",
            raw_metrics={"comments": 3},
            extra={"context_content": {"summary": "Short summary"}, "feed_url": "https://example.com/feed"},
            published_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
            snapshot_date="2026-06-06",
            scraper_slug="example_rss",
            scraper_config_snapshot={"max_items": 10},
        )

        row = item.to_output_dict()

        self.assertEqual(row["url"], "https://example.com/demo")
        self.assertEqual(row["source_type"], "website")
        self.assertEqual(row["sub_source_type"], "example_rss")
        self.assertEqual(row["item_type"], "article")
        self.assertEqual(row["author_id"], "alice")
        self.assertEqual(row["content"], "Main body")
        self.assertEqual(row["context_content"], {"summary": "Short summary"})
        self.assertEqual(row["metrics"], {"comments": 3})
        self.assertEqual(row["extra"], {"feed_url": "https://example.com/feed"})
        self.assertEqual(row["scrape_config_snapshot"], {"max_items": 10})


if __name__ == "__main__":
    unittest.main()
