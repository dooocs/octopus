# scrapers/registry.py
# scraper_type → engine class 映射

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infra.models import BaseScraper

_REGISTRY: dict[str, type] = {}


def register(scraper_type: str):
    def decorator(cls):
        _REGISTRY[scraper_type] = cls
        return cls
    return decorator


def get_engine(scraper_type: str) -> type | None:
    if not _REGISTRY:
        _load_all()
    return _REGISTRY.get(scraper_type)


def list_types() -> list[str]:
    if not _REGISTRY:
        _load_all()
    return list(_REGISTRY.keys())


def _load_all():
    import scrapers.github_trending
    import scrapers.github_search
    import scrapers.hackernews
    import scrapers.rss_feed
    import scrapers.twitter_twscrape
    import scrapers.ai_blog
    import scrapers.community_v2ex
    import scrapers.community_linuxdo
    import scrapers.reddit
    import scrapers.huggingface
    import scrapers.product_hunt
