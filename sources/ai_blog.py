from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import BOOLEAN, INTEGER, JSON_OBJECT, STRING, LegacyEngineAdapter, default_input, input_schema


@register_adapter
class AIBlogAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.ai_blog.AIBlogEngine"
    spec = ChannelSpec(
        scraper="ai_blog",
        label="AI Blog",
        group="Website",
        default_source_type="website",
        default_item_type="article",
        input_schema_version=1,
        input_schema=input_schema(
            source={
                "base_url": STRING,
                "news_url": STRING,
                "link_selector": STRING,
                "author": STRING,
                "source_tag": STRING,
                "metadata": JSON_OBJECT,
            },
            fetch={
                "fetch_window_hours": INTEGER,
                "fetch_full_text": BOOLEAN,
                "full_text_timeout": INTEGER,
                "max_content_chars": INTEGER,
            },
            enrich_names=["full_text"],
        ),
        default_input=default_input(
            source={"base_url": "", "news_url": "", "link_selector": "a[href*='/news/']"},
            fetch={"fetch_window_hours": 0, "fetch_full_text": True, "full_text_timeout": 15, "max_content_chars": 12000},
            enrich=[{"name": "full_text", "when": "always"}],
        ),
        supported_enrichers=["full_text"],
        description="抓取 AI 公司新闻页或博客页面。",
    )
