from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import INTEGER, STRING, LegacyEngineAdapter, default_input, input_schema


@register_adapter
class LinuxDoAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.community_linuxdo.LinuxDoEngine"
    spec = ChannelSpec(
        scraper="community_linuxdo",
        label="LinuxDo",
        group="Community",
        default_source_type="community",
        default_item_type="discussion",
        input_schema_version=1,
        input_schema=input_schema(
            source={"source_tag": STRING},
            fetch={"top_n": INTEGER, "max_replies_to_fetch": INTEGER},
            enrich_names=["top_replies"],
        ),
        default_input=default_input(
            fetch={"top_n": 10, "max_replies_to_fetch": 30},
            enrich=[{"name": "top_replies", "when": "always"}],
        ),
        supported_enrichers=["top_replies"],
        description="抓取 LinuxDo 热门讨论。",
    )
