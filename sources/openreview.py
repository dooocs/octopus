from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceRecord
from core.registry import register_adapter
from infra.http import http_get

from .legacy import INTEGER, STRING_ARRAY, default_input, input_schema

OPENREVIEW_API_URL = "https://api2.openreview.net/notes"


def _value(content: dict[str, Any], key: str, default: Any = None) -> Any:
    raw = content.get(key, default)
    if isinstance(raw, dict) and "value" in raw:
        return raw["value"]
    return raw


def _ms_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


@register_adapter
class OpenReviewAdapter:
    spec = ChannelSpec(
        scraper="openreview",
        label="OpenReview",
        group="Research",
        default_source_type="research",
        default_item_type="paper",
        input_schema_version=1,
        input_schema=input_schema(
            source={"venue_ids": STRING_ARRAY, "invitations": STRING_ARRAY},
            fetch={
                "per_source": INTEGER,
                "limit": INTEGER,
                "sort_by": {"type": "string", "enum": ["reply_count", "published_at"]},
            },
            filters={"min_reply_count": INTEGER},
        ),
        default_input=default_input(
            source={"venue_ids": ["ICLR.cc/2026/Conference"], "invitations": []},
            fetch={"per_source": 50, "limit": 3, "sort_by": "reply_count"},
            filters={"min_reply_count": 0},
        ),
        description="抓取 OpenReview 公开 venue/submission notes，默认按 replyCount 取 top3。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        per_source = int(input_value.fetch.get("per_source") or 50)
        limit = int(input_value.fetch.get("limit") or 3)
        sort_by = str(input_value.fetch.get("sort_by") or "reply_count")
        min_reply_count = int(input_value.filters.get("min_reply_count") or 0)

        records: list[SourceRecord] = []
        seen: set[str] = set()
        for venue_id in input_value.source.get("venue_ids", []):
            records.extend(
                self._fetch_notes(
                    params={"content.venueid": str(venue_id), "limit": per_source, "details": "replyCount"},
                    seen=seen,
                    source_label=str(venue_id),
                    min_reply_count=min_reply_count,
                )
            )
        for invitation in input_value.source.get("invitations", []):
            records.extend(
                self._fetch_notes(
                    params={"invitation": str(invitation), "limit": per_source, "details": "replyCount"},
                    seen=seen,
                    source_label=str(invitation),
                    min_reply_count=min_reply_count,
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
                    int(item.metrics.get("reply_count") or 0),
                    item.source_published_date or datetime.min.replace(tzinfo=timezone.utc),
                ),
                reverse=True,
            )
        return records[:limit]

    def _fetch_notes(
        self,
        *,
        params: dict[str, Any],
        seen: set[str],
        source_label: str,
        min_reply_count: int,
    ) -> list[SourceRecord]:
        response = http_get(
            OPENREVIEW_API_URL,
            params=params,
            timeout=25,
            headers={"User-Agent": "octopus-crawler/0.1"},
        )
        if response.status_code == 400:
            return []
        response.raise_for_status()
        notes = response.json().get("notes") or []
        records: list[SourceRecord] = []
        for note in notes:
            note_id = str(note.get("id") or "")
            if not note_id or note_id in seen:
                continue
            content = note.get("content") or {}
            details = note.get("details") or {}
            reply_count = int(details.get("replyCount") or details.get("reply_count") or 0)
            if reply_count < min_reply_count:
                continue
            title = str(_value(content, "title", "") or "").strip()
            if not title:
                continue
            seen.add(note_id)
            authors = [str(author) for author in _as_list(_value(content, "authors", []))]
            author_ids = [str(author_id) for author_id in _as_list(_value(content, "authorids", []))]
            abstract = str(_value(content, "abstract", "") or "")
            tldr = str(_value(content, "TLDR", "") or _value(content, "tldr", "") or "")
            venue_id = str(_value(content, "venueid", "") or "")
            published_at = _ms_datetime(note.get("pdate")) or _ms_datetime(note.get("tcdate")) or _ms_datetime(note.get("cdate"))
            records.append(
                SourceRecord(
                    identity=note_id,
                    url=f"https://openreview.net/forum?id={note.get('forum') or note_id}",
                    title=title,
                    content="\n\n".join(part for part in [tldr, abstract] if part),
                    metrics={
                        "reply_count": reply_count,
                        "number": note.get("number"),
                        "version": note.get("version"),
                    },
                    extra={
                        "venue_id": venue_id,
                        "source_label": source_label,
                        "forum": note.get("forum"),
                        "keywords": _as_list(_value(content, "keywords", [])),
                        "authors": authors,
                        "author_ids": author_ids,
                        "tcdate": note.get("tcdate"),
                        "tmdate": note.get("tmdate"),
                        "pdate": note.get("pdate"),
                    },
                    author_id=", ".join(authors[:3]),
                    source_published_date=published_at,
                )
            )
        return records

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
            raw_metrics=record.metrics,
            extra=record.extra,
            published_at=record.source_published_date,
        )
