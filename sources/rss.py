from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import BOOLEAN, INTEGER, JSON_OBJECT, STRING, LegacyEngineAdapter, default_input, input_schema


@register_adapter
class RSSAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.rss_feed.RSSFeedEngine"
    spec = ChannelSpec(
        scraper="rss",
        label="RSS Feed",
        group="Website",
        default_source_type="website",
        default_item_type="article",
        input_schema_version=1,
        input_schema=input_schema(
            source={"url": STRING, "source_tag": STRING, "metadata": JSON_OBJECT},
            fetch={
                "max_items": INTEGER,
                "fetch_window_hours": INTEGER,
                "fetch_full_text": BOOLEAN,
                "full_text_timeout": INTEGER,
                "max_content_chars": INTEGER,
            },
            enrich_names=["full_text"],
        ),
        default_input=default_input(
            source={"url": ""},
            fetch={"max_items": 10, "fetch_window_hours": 25, "fetch_full_text": True, "full_text_timeout": 15, "max_content_chars": 12000},
            enrich=[{"name": "full_text", "when": "always"}],
        ),
        supported_enrichers=["full_text"],
        description="抓取标准 RSS/Atom 订阅源。",
    )
