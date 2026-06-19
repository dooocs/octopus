from __future__ import annotations

from typing import Any

from .contracts import ChannelSpec, SourceAdapter
from .validation import validate_channel_spec

_ADAPTERS: dict[str, type[SourceAdapter]] = {}
_LOADED = False


def register_adapter(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    spec = cls.spec
    validate_channel_spec(spec)
    _ADAPTERS[spec.scraper] = cls
    return cls


def get_adapter(scraper: str) -> type[SourceAdapter] | None:
    _load_all()
    return _ADAPTERS.get(scraper)


def list_specs() -> list[ChannelSpec]:
    _load_all()
    return [adapter.spec for adapter in _ADAPTERS.values()]


def list_types() -> list[str]:
    return [spec.scraper for spec in list_specs()]


def export_specs() -> list[dict[str, Any]]:
    return [spec.to_public_dict() for spec in sorted(list_specs(), key=lambda item: item.scraper)]


def _load_all() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    import sources.ai_blog  # noqa: F401
    import sources.community_linuxdo  # noqa: F401
    import sources.community_v2ex  # noqa: F401
    import sources.github  # noqa: F401
    import sources.hackernews  # noqa: F401
    import sources.huggingface  # noqa: F401
    import sources.product_hunt  # noqa: F401
    import sources.reddit  # noqa: F401
    import sources.rss  # noqa: F401
    import sources.twitter  # noqa: F401
