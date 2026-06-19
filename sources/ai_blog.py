from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import INTEGER, STRING, LegacyEngineAdapter, default_input, input_schema


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
            },
            fetch={"fetch_window_hours": INTEGER},
        ),
        default_input=default_input(
            source={"base_url": "", "news_url": "", "link_selector": "a[href*='/news/']"},
            fetch={"fetch_window_hours": 0},
        ),
        description="抓取 AI 公司新闻页或博客页面。",
    )
