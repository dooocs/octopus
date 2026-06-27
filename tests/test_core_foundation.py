from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from core.contracts import (
    ChannelSpec,
    InputConfig,
    RawItem,
    RunContext,
    ScraperConfig,
    SourceAdapterBase,
    SourceRecord,
    raw_item_id_from_url,
)
from core.registry import export_specs, register_adapter
from core.runner import run_config
from pipeline.sinks import JsonlSink
from scripts.migrate_scraper_configs_v1 import convert_flat_input, migrate_row


class _FakeTextResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class InputConfigContractTest(unittest.TestCase):
    def test_input_config_requires_exact_four_top_level_keys(self) -> None:
        valid = {"source": {}, "fetch": {}, "filters": {}, "enrich": []}
        self.assertEqual(InputConfig.from_mapping(valid).to_dict(), valid)

        with self.assertRaises(ValueError):
            InputConfig.from_mapping({"source": {}, "fetch": {}, "filters": {}})

        with self.assertRaises(ValueError):
            InputConfig.from_mapping({**valid, "runtime": {}})


class RawItemContractTest(unittest.TestCase):
    def test_raw_item_id_uses_original_url(self) -> None:
        first = RawItem(
            title="Demo",
            original_url="https://example.com/a",
            source_type="website",
            item_type="article",
            scraper_slug="example",
            identity="native-1",
        )
        same_url_other_identity = RawItem(
            title="Demo",
            original_url="https://example.com/a",
            source_type="website",
            item_type="article",
            scraper_slug="example",
            identity="native-2",
        )
        different_url_same_identity = RawItem(
            title="Demo",
            original_url="https://example.com/b",
            source_type="website",
            item_type="article",
            scraper_slug="example",
            identity="native-1",
        )

        self.assertEqual(first.id, raw_item_id_from_url("https://example.com/a"))
        self.assertEqual(first.id, same_url_other_identity.id)
        self.assertNotEqual(first.id, different_url_same_identity.id)
        self.assertEqual(first.to_output_dict()["extra"], {"native_id": "native-1"})


class SpecExportTest(unittest.TestCase):
    def test_exported_specs_exclude_unimplemented_twitter_nitter(self) -> None:
        scrapers = {item["scraper"] for item in export_specs()}

        self.assertIn("github_search", scrapers)
        self.assertIn("hackernews", scrapers)
        self.assertNotIn("twitter_nitter", scrapers)


class ResearchMetadataTest(unittest.TestCase):
    def test_rss_config_metadata_is_preserved_in_extra(self) -> None:
        rss_text = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <title>Company Feed</title>
    <item>
      <title>Demo filing</title>
      <link>https://example.com/filing</link>
      <description>Demo body</description>
      <pubDate>Fri, 19 Jun 2026 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""
        config = {
            "scraper": "rss",
            "name": "Demo SEC Filings",
            "enabled": True,
            "source_type": "regulatory",
            "sub_source_type": "sec_edgar_demo_filings",
            "item_type": "filing",
            "input": {
                "source": {
                    "url": "https://example.com/feed.xml",
                    "source_tag": "sec_edgar",
                    "metadata": {
                        "company_ticker": "DEMO",
                        "coverage_group": "ai_compute",
                        "theme_tags": ["ai", "semiconductor"],
                    },
                },
                "fetch": {"max_items": 1, "fetch_window_hours": 876000},
                "filters": {},
                "enrich": [],
            },
        }

        with patch("sources.rss.http_get", return_value=_FakeTextResponse(rss_text)):
            result = run_config(config, "2026-06-20")

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["item_type"], "filing")
        self.assertEqual(result.rows[0]["extra"]["source_tag"], "sec_edgar")
        self.assertEqual(result.rows[0]["extra"]["company_ticker"], "DEMO")
        self.assertEqual(result.rows[0]["extra"]["theme_tags"], ["ai", "semiconductor"])


class MigrationContractTest(unittest.TestCase):
    def test_convert_flat_github_search_input_to_four_part_input(self) -> None:
        converted = convert_flat_input(
            "github_search",
            {
                "queries": [{"q": "topic:ai stars:>100", "label": "ai"}],
                "per_page": 30,
                "fetch_window_days": 7,
                "min_stars": 100,
            },
        )

        self.assertEqual(set(converted.keys()), {"source", "fetch", "filters", "enrich"})
        self.assertEqual(converted["source"]["queries"][0]["label"], "ai")
        self.assertEqual(converted["filters"], {"min_stars": 100})

    def test_convert_legacy_five_part_input_drops_runtime(self) -> None:
        converted = convert_flat_input(
            "rss",
            {
                "source": {"url": "https://example.com/feed.xml"},
                "fetch": {"max_items": 5},
                "filters": {},
                "enrich": [],
                "runtime": {"timeout": 10},
            },
        )

        self.assertEqual(
            converted,
            {"source": {"url": "https://example.com/feed.xml"}, "fetch": {"max_items": 5}, "filters": {}, "enrich": []},
        )

    def test_migrate_row_reports_unsupported_scraper(self) -> None:
        result = migrate_row(
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "Legacy Twitter",
                "scraper": "twitter_nitter",
                "input": {"twitter_user": "example"},
            }
        )

        self.assertEqual(result.status, "unsupported")


@register_adapter
class _TestAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="unit_test_adapter",
        label="Unit Test",
        group="Test",
        default_source_type="test",
        default_item_type="article",
        input_schema_version=1,
        input_schema={
            "type": "object",
            "required": ["source", "fetch", "filters", "enrich"],
            "additionalProperties": False,
            "properties": {
                "source": {"type": "object", "additionalProperties": False, "properties": {}},
                "fetch": {"type": "object", "additionalProperties": False, "properties": {}},
                "filters": {"type": "object", "additionalProperties": False, "properties": {}},
                "enrich": {"type": "array", "items": {"type": "object"}},
            },
        },
        default_input={"source": {}, "fetch": {}, "filters": {}, "enrich": []},
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        return [
            SourceRecord(
                identity="native-1",
                url="https://example.com/item",
                title="Example",
                content="Body",
                metrics={"score": 1},
            ),
            SourceRecord(
                identity="native-2",
                url="https://example.com/skip",
                title="Skip",
                content="Skip body",
                metrics={"score": 0},
            )
        ]

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        return [record for record in records if int(record.metrics.get("score") or 0) > 0]

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        ctx.state["enrich_input_count"] = len(records)
        return records

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        return RawItem(
            title=record.title,
            original_url=record.url,
            source_type=config.source_type,
            item_type=config.item_type,
            identity=record.identity,
            body_text=record.content,
            raw_metrics=record.metrics,
        )


class RunnerContractTest(unittest.TestCase):
    def test_run_config_returns_valid_rows(self) -> None:
        result = run_config(
            {
                "scraper": "unit_test_adapter",
                "name": "Unit",
                "enabled": True,
                "source_type": "test",
                "sub_source_type": "unit",
                "item_type": "article",
                "input": {"source": {}, "fetch": {}, "filters": {}, "enrich": []},
            },
            "2026-06-19",
        )

        self.assertEqual(result.items_discovered, 2)
        self.assertEqual(result.items_filtered, 1)
        self.assertEqual(result.items_enriched, 1)
        self.assertEqual(result.state["enrich_input_count"], 1)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["sub_source_type"], "unit")
        self.assertEqual(result.rows[0]["metrics"], {"score": 1})

    def test_run_config_reports_native_stage_order(self) -> None:
        stages: list[str] = []

        run_config(
            {
                "scraper": "unit_test_adapter",
                "name": "Unit",
                "enabled": True,
                "source_type": "test",
                "sub_source_type": "unit",
                "item_type": "article",
                "input": {"source": {}, "fetch": {}, "filters": {}, "enrich": []},
            },
            "2026-06-19",
            on_stage=stages.append,
        )

        self.assertEqual(stages, ["discover", "prune", "enrich", "normalize", "validate_output"])


class SinkContractTest(unittest.TestCase):
    def test_jsonl_sink_writes_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rows.jsonl"
            sink = JsonlSink(path)

            written = sink.write([{"id": "abc", "title": "Demo"}])

            self.assertEqual(written, 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"id": "abc", "title": "Demo"})


if __name__ == "__main__":
    unittest.main()
