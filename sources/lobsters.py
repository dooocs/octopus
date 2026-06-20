from __future__ import annotations

import html
import re
from datetime import datetime, timezone, timedelta

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from core.registry import register_adapter
from infra.http import http_get

from .spec_helpers import INTEGER, STRING, STRING_ARRAY, default_input, input_schema

LOBSTERS_BASE_URL = "https://lobste.rs"


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


def _clean_html(text: str) -> str:
    value = html.unescape(text or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _feed_url(feed: str, tag: str | None = None) -> str:
    if tag:
        return f"{LOBSTERS_BASE_URL}/t/{tag}.json"
    return f"{LOBSTERS_BASE_URL}/{feed}.json"


def _story_url(short_id: str) -> str:
    return f"{LOBSTERS_BASE_URL}/s/{short_id}.json"


@register_adapter
class LobstersAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="lobsters",
        label="Lobsters",
        group="Community",
        default_source_type="community",
        default_item_type="article",
        input_schema_version=1,
        input_schema=input_schema(
            source={"feed": STRING, "tags": STRING_ARRAY},
            fetch={"window_days": INTEGER, "limit": INTEGER, "comments_to_keep": INTEGER},
            filters={
                "min_score": INTEGER,
                "min_comments": INTEGER,
                "tag_whitelist": STRING_ARRAY,
                "tag_blacklist": STRING_ARRAY,
            },
            enrich_names=["top_comments"],
        ),
        default_input=default_input(
            source={"feed": "hottest", "tags": []},
            fetch={"window_days": 2, "limit": 3, "comments_to_keep": 10},
            filters={"min_score": 0, "min_comments": 0, "tag_whitelist": [], "tag_blacklist": []},
            enrich=[{"name": "top_comments", "when": "always"}],
        ),
        supported_enrichers=["top_comments"],
        description="抓取 Lobsters 热榜或指定 tag JSON，默认按 score/comment_count 取 top3。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        feed = str(input_value.source.get("feed") or "hottest")
        tags = [str(tag) for tag in input_value.source.get("tags", [])]
        window_days = int(input_value.fetch.get("window_days") or 0)
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days) if window_days > 0 else None

        records: list[SourceRecord] = []
        seen: set[str] = set()
        sources = tags or [None]
        for tag in sources:
            response = http_get(
                _feed_url(feed, tag),
                timeout=20,
                headers={"User-Agent": "octopus-crawler/0.1"},
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                continue
            for story in data:
                short_id = str(story.get("short_id") or "")
                if not short_id or short_id in seen:
                    continue
                published_at = _parse_datetime(story.get("created_at"))
                if cutoff and published_at and published_at < cutoff:
                    continue
                score = int(story.get("score") or 0)
                comment_count = int(story.get("comment_count") or 0)
                story_tags = {str(item) for item in story.get("tags") or []}
                seen.add(short_id)
                records.append(
                    SourceRecord(
                        identity=f"lobsters:{short_id}",
                        url=str(story.get("url") or story.get("short_id_url") or ""),
                        title=str(story.get("title") or ""),
                        content=str(story.get("description_plain") or story.get("description") or ""),
                        metrics={
                            "score": score,
                            "comment_count": comment_count,
                            "flags": int(story.get("flags") or 0),
                        },
                        extra={
                            "short_id": short_id,
                            "short_id_url": story.get("short_id_url"),
                            "comments_url": story.get("comments_url"),
                            "tags": sorted(story_tags),
                            "feed": feed,
                            "source_tag": tag,
                            "user_is_author": story.get("user_is_author"),
                        },
                        context_content={
                            "top_comments": [],
                        },
                        author_id=str(story.get("submitter_user") or ""),
                        author_url=f"{LOBSTERS_BASE_URL}/u/{story.get('submitter_user')}" if story.get("submitter_user") else "",
                        source_published_date=published_at,
                    )
                )

        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        input_value = config.input
        limit = int(input_value.fetch.get("limit") or 3)
        min_score = int(input_value.filters.get("min_score") or 0)
        min_comments = int(input_value.filters.get("min_comments") or 0)
        whitelist = {str(tag) for tag in input_value.filters.get("tag_whitelist", [])}
        blacklist = {str(tag) for tag in input_value.filters.get("tag_blacklist", [])}

        filtered = []
        for record in records:
            score = int(record.metrics.get("score") or 0)
            comment_count = int(record.metrics.get("comment_count") or 0)
            story_tags = {str(item) for item in record.extra.get("tags") or []}
            if score < min_score or comment_count < min_comments:
                continue
            if whitelist and not (story_tags & whitelist):
                continue
            if blacklist and story_tags & blacklist:
                continue
            filtered.append(record)

        filtered.sort(
            key=lambda item: (
                int(item.metrics.get("score") or 0),
                int(item.metrics.get("comment_count") or 0),
                item.source_published_date or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        return filtered[:limit]

    def _fetch_top_comments(self, short_id: str, limit: int) -> list[dict]:
        if not short_id or limit <= 0:
            return []
        try:
            response = http_get(
                _story_url(short_id),
                timeout=20,
                headers={"User-Agent": "octopus-crawler/0.1"},
            )
            if response.status_code != 200:
                return []
            story = response.json()
            if not isinstance(story, dict):
                return []
        except Exception:
            return []
        comments = []
        for item in story.get("comments") or []:
            if item.get("is_deleted") or item.get("is_moderated"):
                continue
            text = _clean_html(item.get("comment", ""))
            if not text:
                continue
            comments.append(
                {
                    "id": item.get("short_id"),
                    "author": item.get("commenting_user") or item.get("user") or "",
                    "text": text[:1000],
                    "score": int(item.get("score") or 0),
                    "created_at": item.get("created_at"),
                    "parent_comment": item.get("parent_comment"),
                }
            )
        return sorted(comments, key=lambda item: item.get("score") or 0, reverse=True)[:limit]

    def enrich(
        self,
        ctx: RunContext,
        records: list[SourceRecord],
        config: ScraperConfig,
    ) -> list[SourceRecord]:
        comments_to_keep = int(config.input.fetch.get("comments_to_keep") or 10)
        if comments_to_keep <= 0:
            return records
        for record in records:
            short_id = str(record.extra.get("short_id") or "")
            record.context_content["top_comments"] = self._fetch_top_comments(short_id, comments_to_keep)
            record.context_content["top_comments_basis"] = "comment_score"
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
