from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import INTEGER, STRING_ARRAY, LegacyEngineAdapter, default_input, input_schema


@register_adapter
class TwitterAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.twitter_twscrape.TwitterTwscrapeEngine"
    spec = ChannelSpec(
        scraper="twitter_twscrape",
        label="Twitter / X",
        group="Social",
        default_source_type="social",
        default_item_type="post",
        input_schema_version=1,
        input_schema=input_schema(
            source={"watch_accounts": STRING_ARRAY, "tracked_keywords": STRING_ARRAY},
            fetch={"max_age_days": INTEGER},
            filters={"timeline_min_faves": INTEGER, "min_likes": INTEGER},
        ),
        default_input=default_input(
            source={"watch_accounts": [], "tracked_keywords": []},
            fetch={"max_age_days": 2},
            filters={"timeline_min_faves": 50},
        ),
        required_secrets=["TWITTERAPI_IO_KEY"],
        description="抓取关注账号和关键词命中的推文。",
    )
