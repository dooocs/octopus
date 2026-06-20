from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import quote

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from core.registry import register_adapter
from infra.http import http_get

from .spec_helpers import BOOLEAN, INTEGER, STRING, STRING_ARRAY, default_input, input_schema

NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
NPM_PACKAGE_URL = "https://registry.npmjs.org"
NPM_DOWNLOADS_URL = "https://api.npmjs.org/downloads/point"
PYPI_PACKAGE_URL = "https://pypi.org/pypi"
PYPISTATS_RECENT_URL = "https://pypistats.org/api/packages/{package}/recent"

NPM_QUERY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["q", "label"],
        "properties": {"q": STRING, "label": STRING},
        "additionalProperties": False,
    },
}


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


def _safe_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0


def _is_prerelease(version: str) -> bool:
    return bool(re.search(r"(?i)(a|alpha|b|beta|rc|dev|pre)\.?\d*", version))


def _npm_package_path(name: str) -> str:
    return quote(name, safe="@")


def _npm_package_page(name: str) -> str:
    return f"https://www.npmjs.com/package/{quote(name, safe='@/')}"


def _pypi_package_page(name: str, version: str) -> str:
    return f"https://pypi.org/project/{quote(name)}/{quote(version)}/"


def _author_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


@register_adapter
class NpmPackageReleasesAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="npm_package_releases",
        label="npm Package Releases",
        group="Package",
        default_source_type="package_registry",
        default_item_type="package_release",
        input_schema_version=1,
        input_schema=input_schema(
            source={"search_queries": NPM_QUERY_SCHEMA, "packages": STRING_ARRAY},
            fetch={"search_size": INTEGER, "window_days": INTEGER, "limit": INTEGER},
            filters={"min_weekly_downloads": INTEGER, "skip_prerelease": BOOLEAN},
            enrich_names=["release_metadata"],
        ),
        default_input=default_input(
            source={
                "search_queries": [{"q": "keywords:ai", "label": "ai"}],
                "packages": ["openai", "@anthropic-ai/sdk", "ai", "langchain", "@modelcontextprotocol/sdk"],
            },
            fetch={"search_size": 25, "window_days": 7, "limit": 3},
            filters={"min_weekly_downloads": 1000, "skip_prerelease": True},
            enrich=[{"name": "release_metadata", "when": "always"}],
        ),
        supported_enrichers=["release_metadata"],
        description="抓取 npm 近期更新包，保留 weekly/monthly downloads 等原生指标。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        search_size = int(input_value.fetch.get("search_size") or 25)
        window_days = int(input_value.fetch.get("window_days") or 0)
        skip_prerelease = bool(input_value.filters.get("skip_prerelease", True))
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days) if window_days > 0 else None

        records: list[SourceRecord] = []
        seen: set[str] = set()
        for query in input_value.source.get("search_queries", []):
            response = http_get(
                NPM_SEARCH_URL,
                params={"text": query["q"], "size": search_size, "popularity": 0.6, "quality": 0.2, "maintenance": 0.2},
                timeout=20,
                headers={"User-Agent": "octopus-crawler/0.1"},
            )
            response.raise_for_status()
            for obj in response.json().get("objects", []):
                record = self._record_from_search_object(obj, str(query.get("label") or query["q"]), cutoff, skip_prerelease)
                if record is None:
                    continue
                if record.identity in seen:
                    continue
                seen.add(record.identity)
                records.append(record)

        for package_name in input_value.source.get("packages", []):
            record = self._record_from_package(str(package_name), cutoff, skip_prerelease)
            if record is None:
                continue
            if record.identity in seen:
                continue
            seen.add(record.identity)
            records.append(record)

        return records

    def _record_from_search_object(
        self,
        obj: dict[str, Any],
        label: str,
        cutoff: datetime | None,
        skip_prerelease: bool,
    ) -> SourceRecord | None:
        package = obj.get("package") or {}
        name = str(package.get("name") or "")
        version = str(package.get("version") or "")
        if not name or not version:
            return None
        if skip_prerelease and _is_prerelease(version):
            return None
        published_at = _parse_datetime(package.get("date") or obj.get("updated"))
        if cutoff and published_at and published_at < cutoff:
            return None
        downloads = obj.get("downloads") or {}
        score = obj.get("score") or {}
        score_detail = score.get("detail") or {}
        links = package.get("links") or {}
        metrics = {
            "downloads_weekly": _safe_int(downloads.get("weekly")),
            "downloads_monthly": _safe_int(downloads.get("monthly")),
            "dependents": _safe_int(obj.get("dependents")),
            "search_score": obj.get("searchScore"),
            "score_final": score.get("final"),
            "score_popularity": score_detail.get("popularity"),
            "score_quality": score_detail.get("quality"),
            "score_maintenance": score_detail.get("maintenance"),
        }
        return SourceRecord(
            identity=f"npm:{name}@{version}",
            url=str(links.get("npm") or _npm_package_page(name)),
            title=f"{name} {version}",
            content=str(package.get("description") or ""),
            metrics=metrics,
            raw={"package": package},
            extra={
                "ecosystem": "npm",
                "package": name,
                "version": version,
                "query_label": label,
                "keywords": package.get("keywords") or [],
                "license": package.get("license"),
                "publisher": package.get("publisher") or {},
                "maintainers": package.get("maintainers") or [],
                "links": links,
            },
            author_id=((package.get("publisher") or {}).get("username") or ""),
            source_published_date=published_at,
        )

    def _record_from_package(
        self,
        package_name: str,
        cutoff: datetime | None,
        skip_prerelease: bool,
    ) -> SourceRecord | None:
        response = http_get(
            f"{NPM_PACKAGE_URL}/{_npm_package_path(package_name)}",
            timeout=20,
            headers={"User-Agent": "octopus-crawler/0.1"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        version = str((data.get("dist-tags") or {}).get("latest") or "")
        if not version:
            return None
        if skip_prerelease and _is_prerelease(version):
            return None
        published_at = _parse_datetime((data.get("time") or {}).get(version))
        if cutoff and published_at and published_at < cutoff:
            return None
        version_data = (data.get("versions") or {}).get(version) or {}
        return SourceRecord(
            identity=f"npm:{package_name}@{version}",
            url=_npm_package_page(package_name),
            title=f"{package_name} {version}",
            content=str(version_data.get("description") or data.get("description") or ""),
            metrics={
                "downloads_weekly": 0,
                "downloads_monthly": 0,
                "dependents": 0,
            },
            raw={"package_data": data, "version_data": version_data},
            extra={
                "ecosystem": "npm",
                "package": package_name,
                "version": version,
                "query_label": "watched_package",
                "keywords": version_data.get("keywords") or data.get("keywords") or [],
                "license": version_data.get("license") or data.get("license"),
                "repository": version_data.get("repository") or data.get("repository"),
                "homepage": version_data.get("homepage") or data.get("homepage"),
            },
            author_id=_author_name(version_data.get("author") or data.get("author")),
            source_published_date=published_at,
        )

    def _downloads(self, package_name: str, period: str) -> int:
        try:
            response = http_get(
                f"{NPM_DOWNLOADS_URL}/{period}/{_npm_package_path(package_name)}",
                timeout=15,
                headers={"User-Agent": "octopus-crawler/0.1"},
            )
            if response.status_code != 200:
                return 0
            return _safe_int(response.json().get("downloads"))
        except Exception:
            return 0

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        input_value = config.input
        min_weekly_downloads = int(input_value.filters.get("min_weekly_downloads") or 0)
        limit = int(input_value.fetch.get("limit") or 3)
        for record in records:
            if record.extra.get("query_label") == "watched_package":
                package_name = str(record.extra.get("package") or "")
                record.metrics["downloads_weekly"] = self._downloads(package_name, "last-week")
                record.metrics["downloads_monthly"] = self._downloads(package_name, "last-month")
                version_data = record.raw.get("version_data") if isinstance(record.raw, dict) else {}
                package_data = record.raw.get("package_data") if isinstance(record.raw, dict) else {}
                if not isinstance(version_data, dict):
                    version_data = {}
                if not isinstance(package_data, dict):
                    package_data = {}
                readme = str(version_data.get("readme") or package_data.get("readme") or "")
                release_notes = "\n\n".join(
                    part
                    for part in [
                        str(version_data.get("description") or package_data.get("description") or record.content or ""),
                        readme[:4000],
                    ]
                    if part
                )
                record.context_content["release_notes"] = release_notes
                record.context_content["downloads_source"] = "npm_downloads_api"
            else:
                package = record.raw.get("package") if isinstance(record.raw, dict) else {}
                description = ""
                if isinstance(package, dict):
                    description = str(package.get("description") or "")
                record.context_content["release_notes"] = description or record.content
                record.context_content["downloads_source"] = "npm_search_api"

        filtered = [record for record in records if _safe_int(record.metrics.get("downloads_weekly")) >= min_weekly_downloads]
        filtered.sort(
            key=lambda item: (
                _safe_int(item.metrics.get("downloads_weekly")),
                _safe_int(item.metrics.get("dependents")),
                item.source_published_date or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        return filtered[:limit]

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        return RawItem(
            title=record.title,
            original_url=record.url,
            source_name=config.name,
            source_type=config.source_type,
            item_type=config.item_type,
            identity=record.identity,
            author=record.author_id,
            body_text=record.content,
            raw_metrics=record.metrics,
            extra=record.extra,
            context_content=record.context_content,
            published_at=record.source_published_date,
        )


@register_adapter
class PyPIPackageReleasesAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="pypi_package_releases",
        label="PyPI Package Releases",
        group="Package",
        default_source_type="package_registry",
        default_item_type="package_release",
        input_schema_version=1,
        input_schema=input_schema(
            source={"packages": STRING_ARRAY},
            fetch={"window_days": INTEGER, "limit": INTEGER, "fetch_downloads": BOOLEAN},
            filters={"skip_prerelease": BOOLEAN, "skip_yanked": BOOLEAN},
            enrich_names=["release_metadata"],
        ),
        default_input=default_input(
            source={"packages": ["openai", "anthropic", "langchain", "langgraph", "llama-index", "transformers", "vllm"]},
            fetch={"window_days": 14, "limit": 3, "fetch_downloads": True},
            filters={"skip_prerelease": True, "skip_yanked": True},
            enrich=[{"name": "release_metadata", "when": "always"}],
        ),
        supported_enrichers=["release_metadata"],
        description="抓取 PyPI watched packages 的近期 release，并尽量补充 PyPIStats 下载量。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        window_days = int(input_value.fetch.get("window_days") or 0)
        skip_prerelease = bool(input_value.filters.get("skip_prerelease", True))
        skip_yanked = bool(input_value.filters.get("skip_yanked", True))
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days) if window_days > 0 else None

        records: list[SourceRecord] = []
        for package_name in input_value.source.get("packages", []):
            records.extend(self._records_for_package(str(package_name), cutoff, skip_prerelease, skip_yanked))
        return records

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        limit = int(config.input.fetch.get("limit") or 3)
        fetch_downloads = bool(config.input.fetch.get("fetch_downloads", True))
        downloads_by_package: dict[str, dict[str, int]] = {}
        for record in records:
            package_name = str(record.extra.get("package") or "")
            if fetch_downloads and package_name not in downloads_by_package:
                downloads_by_package[package_name] = self._recent_downloads(package_name)
            downloads = downloads_by_package.get(package_name, {})
            downloads_available = any(_safe_int(value) > 0 for value in downloads.values())
            record.metrics["downloads_last_day"] = _safe_int(downloads.get("last_day"))
            record.metrics["downloads_last_week"] = _safe_int(downloads.get("last_week"))
            record.metrics["downloads_last_month"] = _safe_int(downloads.get("last_month"))
            record.metrics["downloads_available"] = downloads_available
            release_notes = record.raw.get("release_notes") if isinstance(record.raw, dict) else ""
            record.context_content["release_notes"] = str(release_notes or record.content or "")
            record.context_content["downloads_source"] = "pypistats_recent_api" if downloads_available else "pypistats_unavailable"
        records.sort(
            key=lambda item: (
                _safe_int(item.metrics.get("downloads_last_month")),
                _safe_int(item.metrics.get("downloads_last_week")),
                item.source_published_date or datetime.min.replace(tzinfo=timezone.utc),
                _safe_int(item.metrics.get("file_count")),
            ),
            reverse=True,
        )
        return records[:limit]

    def _records_for_package(
        self,
        package_name: str,
        cutoff: datetime | None,
        skip_prerelease: bool,
        skip_yanked: bool,
    ) -> list[SourceRecord]:
        response = http_get(
            f"{PYPI_PACKAGE_URL}/{quote(package_name)}/json",
            timeout=20,
            headers={"User-Agent": "octopus-crawler/0.1"},
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
        info = data.get("info") or {}
        vulnerabilities = data.get("vulnerabilities") or []
        release_notes = "\n\n".join(
            part
            for part in [str(info.get("summary") or ""), str(info.get("description") or "")[:4000]]
            if part
        )
        records: list[SourceRecord] = []
        for version, files in (data.get("releases") or {}).items():
            if skip_prerelease and _is_prerelease(str(version)):
                continue
            if not isinstance(files, list) or not files:
                continue
            uploaded_dates = [_parse_datetime(item.get("upload_time_iso_8601")) for item in files]
            uploaded_dates = [item for item in uploaded_dates if item is not None]
            if not uploaded_dates:
                continue
            published_at = max(uploaded_dates)
            if cutoff and published_at < cutoff:
                continue
            yanked_count = sum(1 for item in files if item.get("yanked"))
            if skip_yanked and yanked_count == len(files):
                continue
            records.append(
                SourceRecord(
                    identity=f"pypi:{package_name}=={version}",
                    url=_pypi_package_page(package_name, str(version)),
                    title=f"{package_name} {version}",
                    content="\n\n".join(
                        part
                        for part in [str(info.get("summary") or ""), str(info.get("description") or "")[:4000]]
                        if part
                    ),
                    metrics={
                        "file_count": len(files),
                        "yanked_count": yanked_count,
                        "vulnerability_count": len(vulnerabilities),
                        "classifier_count": len(info.get("classifiers") or []),
                        "downloads_last_day": 0,
                        "downloads_last_week": 0,
                        "downloads_last_month": 0,
                        "downloads_available": False,
                    },
                    raw={"release_notes": release_notes},
                    extra={
                        "ecosystem": "pypi",
                        "package": package_name,
                        "version": version,
                        "license": info.get("license_expression") or info.get("license"),
                        "requires_python": info.get("requires_python"),
                        "project_urls": info.get("project_urls") or {},
                        "files": [
                            {
                                "filename": item.get("filename"),
                                "packagetype": item.get("packagetype"),
                                "size": item.get("size"),
                                "yanked": item.get("yanked"),
                            }
                            for item in files[:10]
                        ],
                    },
                    author_id=str(info.get("author") or info.get("author_email") or ""),
                    source_published_date=published_at,
                )
            )
        return records

    def _recent_downloads(self, package_name: str) -> dict[str, int]:
        try:
            response = http_get(
                PYPISTATS_RECENT_URL.format(package=quote(package_name)),
                timeout=15,
                headers={"User-Agent": "octopus-crawler/0.1"},
            )
            if response.status_code != 200:
                return {}
            data = response.json().get("data") or {}
            return {
                "last_day": _safe_int(data.get("last_day")),
                "last_week": _safe_int(data.get("last_week")),
                "last_month": _safe_int(data.get("last_month")),
            }
        except Exception:
            return {}

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        return RawItem(
            title=record.title,
            original_url=record.url,
            source_name=config.name,
            source_type=config.source_type,
            item_type=config.item_type,
            identity=record.identity,
            author=record.author_id,
            body_text=record.content,
            raw_metrics=record.metrics,
            extra=record.extra,
            context_content=record.context_content,
            published_at=record.source_published_date,
        )
