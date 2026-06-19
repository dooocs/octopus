from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import INTEGER, STRING, STRING_ARRAY, LegacyEngineAdapter, default_input, input_schema


@register_adapter
class HackerNewsAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.hackernews.HackerNewsEngine"
    spec = ChannelSpec(
        scraper="hackernews",
        label="HackerNews",
        group="Community",
        default_source_type="community",
        default_item_type="article",
        input_schema_version=1,
        input_schema=input_schema(
            source={"feed": STRING},
            fetch={"new_n": INTEGER, "cutoff_hours": INTEGER, "fetch_workers": INTEGER, "skip_domains": STRING_ARRAY},
            filters={"min_score": INTEGER},
            enrich_names=["article_body"],
        ),
        default_input=default_input(
            source={"feed": "newstories"},
            fetch={
                "new_n": 100,
                "cutoff_hours": 36,
                "fetch_workers": 5,
                "skip_domains": ["twitter.com", "x.com", "medium.com", "zhihu.com"],
            },
            filters={"min_score": 50},
            enrich=[{"name": "article_body", "when": "has_external_url"}],
        ),
        supported_enrichers=["article_body"],
        description="抓取 Hacker News 高分新帖。",
    )
