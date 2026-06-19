from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import INTEGER, STRING_ARRAY, LegacyEngineAdapter, default_input, input_schema


@register_adapter
class HuggingFaceModelsAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.huggingface.HuggingFaceModelsEngine"
    spec = ChannelSpec(
        scraper="hf_model",
        label="Hugging Face Models",
        group="AI",
        default_source_type="model_hub",
        default_item_type="model",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"limit": INTEGER, "max_retries": INTEGER},
            filters={
                "min_likes": INTEGER,
                "min_downloads": INTEGER,
                "quant_suffixes": STRING_ARRAY,
                "deriv_suffixes": STRING_ARRAY,
            },
        ),
        default_input=default_input(
            fetch={"limit": 3, "max_retries": 3},
            filters={"min_likes": 50, "min_downloads": 1000},
        ),
        description="抓取 Hugging Face 模型趋势。",
    )


@register_adapter
class HuggingFacePapersAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.huggingface.HuggingFacePapersEngine"
    spec = ChannelSpec(
        scraper="hf_papers",
        label="Hugging Face Papers",
        group="AI",
        default_source_type="model_hub",
        default_item_type="paper",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"top_n": INTEGER, "max_retries": INTEGER},
        ),
        default_input=default_input(fetch={"top_n": 3, "max_retries": 3}),
        description="抓取 Hugging Face Daily Papers。",
    )
