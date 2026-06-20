from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any

import feedparser

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from core.registry import register_adapter
from infra.http import http_get

from .spec_helpers import INTEGER, STRING, STRING_ARRAY, default_input, input_schema

ARXIV_API_URL = "https://export.arxiv.org/api/query"

QUERY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["q", "label"],
        "properties": {"q": STRING, "label": STRING},
        "additionalProperties": False,
    },
}


def _parse_feed_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    try:
        return datetime(*value[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _entry_categories(entry: Any) -> list[str]:
    return [tag.get("term") for tag in getattr(entry, "tags", []) if tag.get("term")]


def _primary_category(entry: Any, categories: list[str]) -> str:
    primary = getattr(entry, "arxiv_primary_category", None)
    if isinstance(primary, dict) and primary.get("term"):
        return str(primary["term"])
    return categories[0] if categories else ""


def _entry_authors(entry: Any) -> list[str]:
    authors = []
    for author in getattr(entry, "authors", []) or []:
        name = author.get("name") if isinstance(author, dict) else getattr(author, "name", "")
        if name:
            authors.append(str(name))
    if not authors and getattr(entry, "author", ""):
        authors.append(str(entry.author))
    return authors


def _arxiv_id(entry_id: str) -> str:
    return entry_id.rstrip("/").rsplit("/", 1)[-1]


def _pdf_url(entry: Any) -> str:
    for link in getattr(entry, "links", []) or []:
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            return str(link.get("href") or "")
    entry_id = getattr(entry, "id", "")
    return entry_id.replace("/abs/", "/pdf/") if entry_id else ""


def _matches_terms(title: str, summary: str, include_terms: list[str], exclude_terms: list[str]) -> bool:
    haystack = f"{title}\n{summary}".lower()
    if include_terms and not any(term.lower() in haystack for term in include_terms):
        return False
    if exclude_terms and any(term.lower() in haystack for term in exclude_terms):
        return False
    return True


@register_adapter
class ArxivAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="arxiv",
        label="arXiv",
        group="Research",
        default_source_type="research",
        default_item_type="paper",
        input_schema_version=1,
        input_schema=input_schema(
            source={"queries": QUERY_SCHEMA},
            fetch={
                "max_results": INTEGER,
                "limit": INTEGER,
                "window_days": INTEGER,
                "sort_by": {"type": "string", "enum": ["relevance", "lastUpdatedDate", "submittedDate"]},
                "sort_order": {"type": "string", "enum": ["ascending", "descending"]},
            },
            filters={"include_terms": STRING_ARRAY, "exclude_terms": STRING_ARRAY},
        ),
        default_input=default_input(
            source={"queries": [{"q": "cat:cs.AI OR cat:cs.LG OR cat:cs.CL", "label": "ai_ml_cl"}]},
            fetch={"max_results": 25, "limit": 3, "window_days": 3, "sort_by": "submittedDate", "sort_order": "descending"},
            filters={"include_terms": [], "exclude_terms": []},
        ),
        description="按 arXiv 查询抓取近期论文，默认每天取最近 top3。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        max_results = int(input_value.fetch.get("max_results") or 25)
        window_days = int(input_value.fetch.get("window_days") or 0)
        sort_by = str(input_value.fetch.get("sort_by") or "submittedDate")
        sort_order = str(input_value.fetch.get("sort_order") or "descending")
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days) if window_days > 0 else None

        records: list[SourceRecord] = []
        seen: set[str] = set()
        for query in input_value.source.get("queries", []):
            label = str(query.get("label") or query.get("q") or "query")
            response = http_get(
                ARXIV_API_URL,
                params={
                    "search_query": query["q"],
                    "start": 0,
                    "max_results": max_results,
                    "sortBy": sort_by,
                    "sortOrder": sort_order,
                },
                timeout=20,
                headers={"User-Agent": "octopus-crawler/0.1 (mailto:ops@example.com)"},
            )
            response.raise_for_status()
            feed = feedparser.parse(response.text)
            for entry in feed.entries:
                entry_id = str(getattr(entry, "id", "") or "")
                native_id = _arxiv_id(entry_id)
                if not native_id or native_id in seen:
                    continue
                published_at = _parse_feed_date(getattr(entry, "published_parsed", None)) or _parse_feed_date(
                    getattr(entry, "published", None)
                )
                if cutoff and published_at and published_at < cutoff:
                    continue
                title = _text(getattr(entry, "title", ""))
                summary = _text(getattr(entry, "summary", ""))
                if not title:
                    continue
                seen.add(native_id)
                categories = _entry_categories(entry)
                authors = _entry_authors(entry)
                updated_at = _parse_feed_date(getattr(entry, "updated_parsed", None)) or _parse_feed_date(
                    getattr(entry, "updated", None)
                )
                pdf_url = _pdf_url(entry)
                version_match = re.search(r"v(\d+)$", native_id)
                records.append(
                    SourceRecord(
                        identity=native_id,
                        url=entry_id.replace("http://", "https://") or f"https://arxiv.org/abs/{native_id}",
                        title=title,
                        content=summary,
                        metrics={
                            "author_count": len(authors),
                            "category_count": len(categories),
                            "version": int(version_match.group(1)) if version_match else None,
                        },
                        extra={
                            "query_label": label,
                            "arxiv_id": native_id,
                            "authors": authors,
                            "categories": categories,
                            "primary_category": _primary_category(entry, categories),
                            "pdf_url": pdf_url,
                            "updated_at": updated_at.isoformat() if updated_at else None,
                        },
                        author_id=", ".join(authors[:3]),
                        source_published_date=published_at,
                    )
                )

        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        limit = int(input_value.fetch.get("limit") or 3)
        include_terms = [str(term) for term in input_value.filters.get("include_terms", [])]
        exclude_terms = [str(term) for term in input_value.filters.get("exclude_terms", [])]
        filtered = [
            record
            for record in records
            if _matches_terms(record.title, record.content, include_terms, exclude_terms)
        ]
        filtered.sort(key=lambda item: item.source_published_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return filtered[:limit]

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
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
            body_text=record.content,
            raw_metrics={key: value for key, value in record.metrics.items() if value is not None},
            extra=record.extra,
            published_at=record.source_published_date,
        )
