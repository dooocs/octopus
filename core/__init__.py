from __future__ import annotations

from .contracts import (
    BaseScraper,
    ChannelSpec,
    InputConfig,
    RawItem,
    RunContext,
    ScrapeTaskResult,
    ScraperConfig,
    SourceRecord,
)
from .registry import export_specs, get_adapter, list_specs, list_types, register_adapter
from .runner import run_config, run_scrapers

__all__ = [
    "BaseScraper",
    "ChannelSpec",
    "InputConfig",
    "RawItem",
    "RunContext",
    "ScrapeTaskResult",
    "ScraperConfig",
    "SourceRecord",
    "export_specs",
    "get_adapter",
    "list_specs",
    "list_types",
    "register_adapter",
    "run_config",
    "run_scrapers",
]
