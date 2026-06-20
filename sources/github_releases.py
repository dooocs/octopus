from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceRecord
from core.registry import register_adapter
from infra.http import http_get

from .legacy import BOOLEAN, INTEGER, STRING_ARRAY, default_input, input_schema

GITHUB_API_URL = "https://api.github.com"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "octopus-crawler/0.1",
    }
    token = os.getenv("GH_MODELS_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _asset_downloads(release: dict[str, Any]) -> int:
    return sum(int(asset.get("download_count") or 0) for asset in release.get("assets") or [])


@register_adapter
class GitHubReleasesAdapter:
    spec = ChannelSpec(
        scraper="github_releases",
        label="GitHub Releases",
        group="Code",
        default_source_type="code_host",
        default_item_type="release",
        input_schema_version=1,
        input_schema=input_schema(
            source={"repositories": STRING_ARRAY},
            fetch={
                "releases_per_repo": INTEGER,
                "window_days": INTEGER,
                "limit": INTEGER,
                "sort_by": {"type": "string", "enum": ["asset_downloads", "published_at"]},
            },
            filters={"skip_prerelease": BOOLEAN, "min_asset_downloads": INTEGER},
            enrich_names=["release_details"],
        ),
        default_input=default_input(
            source={
                "repositories": [
                    "openai/openai-python",
                    "anthropics/anthropic-sdk-python",
                    "langchain-ai/langchain",
                    "langchain-ai/langgraph",
                    "huggingface/transformers",
                    "vllm-project/vllm",
                    "ollama/ollama",
                    "modelcontextprotocol/python-sdk",
                ]
            },
            fetch={"releases_per_repo": 5, "window_days": 14, "limit": 3, "sort_by": "asset_downloads"},
            filters={"skip_prerelease": True, "min_asset_downloads": 0},
            enrich=[{"name": "release_details", "when": "always"}],
        ),
        supported_enrichers=["release_details"],
        description="抓取 watched GitHub repositories 的近期 release，默认每天取 top3。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        repositories = [str(repo).strip() for repo in input_value.source.get("repositories", []) if str(repo).strip()]
        releases_per_repo = int(input_value.fetch.get("releases_per_repo") or 5)
        window_days = int(input_value.fetch.get("window_days") or 0)
        limit = int(input_value.fetch.get("limit") or 3)
        sort_by = str(input_value.fetch.get("sort_by") or "asset_downloads")
        skip_prerelease = bool(input_value.filters.get("skip_prerelease", True))
        min_asset_downloads = int(input_value.filters.get("min_asset_downloads") or 0)
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days) if window_days > 0 else None

        records: list[SourceRecord] = []
        for repo in repositories:
            response = http_get(
                f"{GITHUB_API_URL}/repos/{repo}/releases",
                params={"per_page": releases_per_repo},
                headers=_headers(),
                timeout=20,
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            releases = response.json()
            if not isinstance(releases, list):
                continue
            for release in releases:
                published_at = _parse_datetime(release.get("published_at"))
                if cutoff and published_at and published_at < cutoff:
                    continue
                if skip_prerelease and release.get("prerelease"):
                    continue
                downloads = _asset_downloads(release)
                if downloads < min_asset_downloads:
                    continue
                release_id = str(release.get("id") or f"{repo}:{release.get('tag_name')}")
                assets = release.get("assets") or []
                records.append(
                    SourceRecord(
                        identity=release_id,
                        url=str(release.get("html_url") or f"https://github.com/{repo}/releases"),
                        title=f"{repo} {release.get('name') or release.get('tag_name') or 'release'}",
                        content=str(release.get("body") or "")[:8000],
                        raw={"release": release},
                        metrics={
                            "github_release_id": release.get("id"),
                            "asset_downloads": downloads,
                            "asset_count": len(assets),
                            "prerelease": bool(release.get("prerelease")),
                        },
                        extra={
                            "repository": repo,
                            "tag_name": release.get("tag_name"),
                            "target_commitish": release.get("target_commitish"),
                            "assets": [
                                {
                                    "name": asset.get("name"),
                                    "download_count": asset.get("download_count"),
                                    "browser_download_url": asset.get("browser_download_url"),
                                }
                                for asset in assets[:10]
                            ],
                        },
                        context_content={},
                        author_id=(release.get("author") or {}).get("login") or "",
                        author_url=(release.get("author") or {}).get("html_url") or "",
                        source_published_date=published_at,
                    )
                )

        if sort_by == "published_at":
            records.sort(
                key=lambda item: item.source_published_date or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
        else:
            records.sort(
                key=lambda item: (
                    int(item.metrics.get("asset_downloads") or 0),
                    item.source_published_date or datetime.min.replace(tzinfo=timezone.utc),
                ),
                reverse=True,
            )
        return records[:limit]

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        for record in records:
            release = record.raw.get("release") if isinstance(record.raw, dict) else {}
            if not isinstance(release, dict):
                continue
            assets = release.get("assets") or []
            record.context_content["release_notes"] = str(release.get("body") or "")[:12000]
            record.context_content["asset_downloads"] = int(record.metrics.get("asset_downloads") or 0)
            record.context_content["assets"] = [
                {
                    "name": asset.get("name"),
                    "download_count": asset.get("download_count"),
                    "browser_download_url": asset.get("browser_download_url"),
                }
                for asset in assets[:10]
            ]
        return records

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        return RawItem(
            title=record.title,
            original_url=record.url,
            source_name=config.name,
            source_type=config.source_type,
            item_type=config.item_type,
            identity=record.identity,
            author=record.author_id,
            author_url=record.author_url,
            body_text=record.content,
            raw_metrics=record.metrics,
            extra=record.extra,
            context_content=record.context_content,
            published_at=record.source_published_date,
        )
