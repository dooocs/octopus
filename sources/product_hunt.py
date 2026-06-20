from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import INTEGER, STRING, LegacyEngineAdapter, default_input, input_schema


@register_adapter
class ProductHuntAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.product_hunt.ProductHuntEngine"
    spec = ChannelSpec(
        scraper="product_hunt",
        label="Product Hunt",
        group="Product",
        default_source_type="product_platform",
        default_item_type="product",
        input_schema_version=1,
        input_schema=input_schema(
            source={"api_token": STRING},
            fetch={"max_retries": INTEGER},
            filters={
                "min_votes": INTEGER,
                "topic_whitelist": {"type": "array", "items": STRING},
                "topic_blacklist": {"type": "array", "items": STRING},
            },
            enrich_names=["product_comments"],
        ),
        default_input=default_input(
            filters={"min_votes": 200, "topic_whitelist": [], "topic_blacklist": []},
            fetch={"max_retries": 3},
            enrich=[{"name": "product_comments", "when": "always"}],
        ),
        required_secrets=["PRODUCTHUNT_TOKEN"],
        supported_enrichers=["product_comments"],
        description="抓取 Product Hunt 高票新产品。",
    )
