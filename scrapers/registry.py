from __future__ import annotations

from typing import Any

from core.registry import export_specs, get_adapter, list_specs, list_types

_LEGACY_REGISTRY: dict[str, type] = {}


def register(scraper_type: str):
    """Compatibility decorator for legacy scraper modules.

    New runtime code uses `core.registry` and SourceAdapter classes. Legacy
    modules still import this decorator during adapter bridge imports.
    """

    def decorator(cls: type) -> type:
        _LEGACY_REGISTRY[scraper_type] = cls
        return cls

    return decorator


def get_engine(scraper_type: str) -> type[Any] | None:
    return get_adapter(scraper_type)


def get_legacy_engine(scraper_type: str) -> type[Any] | None:
    return _LEGACY_REGISTRY.get(scraper_type)


__all__ = [
    "export_specs",
    "get_adapter",
    "get_engine",
    "get_legacy_engine",
    "list_specs",
    "list_types",
    "register",
]
