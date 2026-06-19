from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import BOOLEAN, INTEGER, STRING, LegacyEngineAdapter, default_input, input_schema


@register_adapter
class RedditAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.reddit.RedditEngine"
    spec = ChannelSpec(
        scraper="reddit",
        label="Reddit",
        group="Community",
        default_source_type="community",
        default_item_type="discussion",
        input_schema_version=1,
        input_schema=input_schema(
            source={"subreddit": STRING},
            filters={
                "min_score": INTEGER,
                "skip_nsfw": BOOLEAN,
                "skip_stickied": BOOLEAN,
                "skip_discussion_below": INTEGER,
                "skip_self_text_below": INTEGER,
            },
            fetch={"max_retries": INTEGER},
        ),
        default_input=default_input(
            source={"subreddit": "LocalLLaMA"},
            filters={
                "min_score": 50,
                "skip_nsfw": True,
                "skip_stickied": True,
                "skip_discussion_below": 100,
                "skip_self_text_below": 200,
            },
        ),
        description="抓取指定 subreddit 的高分讨论。",
    )
