from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import INTEGER, STRING, LegacyEngineAdapter, default_input, input_schema


@register_adapter
class V2EXAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.community_v2ex.V2EXEngine"
    spec = ChannelSpec(
        scraper="community_v2ex",
        label="V2EX",
        group="Community",
        default_source_type="community",
        default_item_type="discussion",
        input_schema_version=1,
        input_schema=input_schema(
            source={"source_tag": STRING},
            fetch={
                "top_n": INTEGER,
                "top_clicked_limit": INTEGER,
                "max_replies_to_fetch": INTEGER,
                "max_replies_to_keep": INTEGER,
            },
            enrich_names=["top_replies"],
        ),
        default_input=default_input(
            fetch={"top_n": 10, "top_clicked_limit": 10, "max_replies_to_fetch": 30, "max_replies_to_keep": 10},
            enrich=[{"name": "top_replies", "when": "always"}],
        ),
        supported_enrichers=["top_replies"],
        description="抓取 V2EX 热门技术讨论。",
    )
