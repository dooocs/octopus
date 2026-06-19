from __future__ import annotations

from core.contracts import ChannelSpec
from core.registry import register_adapter

from .legacy import INTEGER, STRING, LegacyEngineAdapter, default_input, input_schema

QUERY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["q", "label"],
        "properties": {"q": STRING, "label": STRING},
        "additionalProperties": False,
    },
}


@register_adapter
class GitHubTrendingAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.github_trending.GitHubTrendingEngine"
    spec = ChannelSpec(
        scraper="github_trending",
        label="GitHub Trending",
        group="Code",
        default_source_type="code_host",
        default_item_type="repo",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"timeout": INTEGER},
            enrich_names=["github_readme", "github_languages", "github_images", "star_history"],
        ),
        default_input=default_input(
            fetch={"timeout": 15},
            enrich=[
                {"name": "github_readme", "when": "always"},
                {"name": "github_languages", "when": "always"},
                {"name": "github_images", "when": "has_readme"},
                {"name": "star_history", "when": "always"},
            ],
        ),
        required_secrets=["GH_MODELS_TOKEN"],
        supported_enrichers=["github_readme", "github_languages", "github_images", "star_history"],
        description="抓取 GitHub Trending 仓库榜单。",
    )


@register_adapter
class GitHubSearchAdapter(LegacyEngineAdapter):
    engine_path = "scrapers.github_search.GitHubSearchEngine"
    spec = ChannelSpec(
        scraper="github_search",
        label="GitHub Search",
        group="Code",
        default_source_type="code_host",
        default_item_type="repo",
        input_schema_version=1,
        input_schema=input_schema(
            source={"queries": QUERY_SCHEMA},
            fetch={
                "per_page": INTEGER,
                "fetch_window_days": INTEGER,
                "max_readme_images": INTEGER,
                "badge_patterns": {"type": "array", "items": STRING},
            },
            filters={"min_stars": INTEGER},
            enrich_names=["github_readme", "github_languages", "github_images", "star_history"],
        ),
        default_input=default_input(
            source={"queries": [{"q": "topic:ai stars:>100", "label": "ai"}]},
            fetch={"per_page": 30, "fetch_window_days": 7},
            filters={"min_stars": 100},
            enrich=[
                {"name": "github_readme", "when": "always"},
                {"name": "github_languages", "when": "always"},
                {"name": "github_images", "when": "has_readme"},
                {"name": "star_history", "when": "always"},
            ],
        ),
        required_secrets=["GH_MODELS_TOKEN"],
        supported_enrichers=["github_readme", "github_languages", "github_images", "star_history"],
        description="按关键词搜索近期 GitHub 仓库。",
    )
