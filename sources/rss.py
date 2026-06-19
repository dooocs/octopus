from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import INTEGER, LegacyEngineAdapter, default_input, input_schema


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
            source={"url": {"type": "string"}},
            fetch={"max_items": INTEGER, "fetch_window_hours": INTEGER},
        ),
        default_input=default_input(
            source={"url": ""},
            fetch={"max_items": 10, "fetch_window_hours": 25},
        ),
        description="抓取标准 RSS/Atom 订阅源。",
    )
