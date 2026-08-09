from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get

from .spec_helpers import INTEGER, STRING_ARRAY, default_input, input_schema

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
class OpenReviewAdapter(SourceAdapterBase):
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
                "reply_limit": INTEGER,
                "sort_by": {"type": "string", "enum": ["reply_count", "published_at"]},
            },
            filters={"min_reply_count": INTEGER},
            enrich_names=["openreview_replies"],
        ),
        default_input=default_input(
            source={"venue_ids": ["ICLR.cc/2026/Conference"], "invitations": []},
            fetch={"per_source": 50, "limit": 3, "reply_limit": 10, "sort_by": "reply_count"},
            filters={"min_reply_count": 0},
            enrich=[{"name": "openreview_replies", "when": "always"}],
        ),
        supported_enrichers=["openreview_replies"],
        description="抓取 OpenReview 公开 venue/submission notes，默认按 replyCount 取 top3。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        per_source = int(input_value.fetch.get("per_source") or 50)

        records: list[SourceRecord] = []
        seen: set[str] = set()
        for venue_id in input_value.source.get("venue_ids", []):
            records.extend(
                self._fetch_notes(
                    params={"content.venueid": str(venue_id), "limit": per_source, "details": "replyCount"},
                    seen=seen,
                    source_label=str(venue_id),
                )
            )
        for invitation in input_value.source.get("invitations", []):
            records.extend(
                self._fetch_notes(
                    params={"invitation": str(invitation), "limit": per_source, "details": "replyCount"},
                    seen=seen,
                    source_label=str(invitation),
                )
            )

        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        limit = int(input_value.fetch.get("limit") or 3)
        sort_by = str(input_value.fetch.get("sort_by") or "reply_count")
        min_reply_count = int(input_value.filters.get("min_reply_count") or 0)
        filtered = [
            record
            for record in records
            if int(record.metrics.get("reply_count") or 0) >= min_reply_count
        ]
        if sort_by == "published_at":
            filtered.sort(
                key=lambda item: item.source_published_date or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
        else:
            filtered.sort(
                key=lambda item: (
                    int(item.metrics.get("reply_count") or 0),
                    item.source_published_date or datetime.min.replace(tzinfo=timezone.utc),
                ),
                reverse=True,
            )
        return filtered[:limit]

    def _fetch_notes(
        self,
        *,
        params: dict[str, Any],
        seen: set[str],
        source_label: str,
    ) -> list[SourceRecord]:
        response = http_get(
            OPENREVIEW_API_URL,
            params=params,
            timeout=12,
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
            forum = str(note.get("forum") or note_id)
            records.append(
                SourceRecord(
                    identity=note_id,
                    url=f"https://openreview.net/forum?id={forum}",
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
                        "forum": forum,
                        "keywords": _as_list(_value(content, "keywords", [])),
                        "authors": authors,
                        "author_ids": author_ids,
                        "tcdate": note.get("tcdate"),
                        "tmdate": note.get("tmdate"),
                        "pdate": note.get("pdate"),
                    },
                    context_content={
                        "top_replies": [],
                    },
                    author_id=", ".join(authors[:3]),
                    source_published_date=published_at,
                )
            )
        return records

    def _fetch_replies(self, forum: str, original_note_id: str, limit: int) -> list[dict]:
        if not forum or limit <= 0:
            return []
        try:
            response = http_get(
                OPENREVIEW_API_URL,
                params={"forum": forum, "limit": max(limit * 3, limit)},
                timeout=12,
                headers={"User-Agent": "octopus-crawler/0.1"},
            )
            if response.status_code != 200:
                return []
            notes = response.json().get("notes") or []
        except Exception:
            return []

        replies: list[dict] = []
        for note in notes:
            note_id = str(note.get("id") or "")
            if not note_id or note_id == original_note_id:
                continue
            content = note.get("content") or {}
            flat_content = {key: _value(content, key) for key in content.keys()}
            text = (
                flat_content.get("comment")
                or flat_content.get("review")
                or flat_content.get("summary")
                or flat_content.get("decision")
                or flat_content.get("title")
                or ""
            )
            if not text:
                continue
            replies.append(
                {
                    "id": note_id,
                    "replyto": note.get("replyto"),
                    "invitation": (note.get("invitations") or [""])[0],
                    "signatures": note.get("signatures") or [],
                    "text": str(text)[:1500],
                    "rating": flat_content.get("rating"),
                    "confidence": flat_content.get("confidence"),
                    "recommendation": flat_content.get("recommendation"),
                    "created_at": _ms_datetime(note.get("tcdate")).isoformat() if _ms_datetime(note.get("tcdate")) else None,
                }
            )
        replies.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return replies[:limit]

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        reply_limit = int(config.input.fetch.get("reply_limit") or 10)
        if reply_limit <= 0:
            return records
        for record in records:
            forum = str(record.extra.get("forum") or record.identity)
            record.context_content["top_replies"] = self._fetch_replies(forum, record.identity, reply_limit)
            record.context_content["top_replies_basis"] = "openreview_public_forum_replies"
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
            context_content=record.context_content,
            published_at=record.source_published_date,
        )
