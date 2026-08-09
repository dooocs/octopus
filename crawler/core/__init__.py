from __future__ import annotations

from .contracts import (
    ChannelSpec,
    InputConfig,
    RawItem,
    RunContext,
    ScrapeTaskResult,
    ScraperConfig,
    SourceAdapterBase,
    SourceRecord,
)
from .registry import export_specs, get_adapter, list_specs, list_types, register_adapter
from .runner import run_config, run_scrapers

__all__ = [
    "ChannelSpec",
    "InputConfig",
    "RawItem",
    "RunContext",
    "ScrapeTaskResult",
    "ScraperConfig",
    "SourceAdapterBase",
    "SourceRecord",
    "export_specs",
    "get_adapter",
    "list_specs",
    "list_types",
    "register_adapter",
    "run_config",
    "run_scrapers",
]
