from __future__ import annotations

import re
from datetime import datetime, timezone

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord, enrich_enabled
from core.registry import register_adapter
from infra.http import http_get

from .spec_helpers import INTEGER, STRING, default_input, input_schema

V2EX_HOT_API = "https://www.v2ex.com/api/topics/hot.json"
V2EX_REPLIES_API = "https://www.v2ex.com/api/replies/show.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"}


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _fetch_replies(topic_id: int, max_fetch: int) -> list[dict]:
    try:
        resp = http_get(V2EX_REPLIES_API, params={"topic_id": topic_id, "p": 1}, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        replies = resp.json()
        return replies[:max_fetch] if isinstance(replies, list) else []
    except Exception:
        return []


def _top_replies(replies: list[dict], max_keep: int) -> list[dict]:
    top: list[dict] = []
    for reply in sorted(replies, key=lambda item: item.get("thanked", 0), reverse=True)[:max_keep]:
        author = reply.get("member", {}).get("username", "匿名")
        text = _clean_html(reply.get("content_rendered") or reply.get("content") or "")
        if not text:
            continue
        top.append(
            {
                "id": reply.get("id"),
                "author": author,
                "text": text[:1000],
                "thanked": reply.get("thanked", 0),
                "created_at": datetime.fromtimestamp(reply.get("created", 0), tz=timezone.utc).isoformat()
                if reply.get("created")
                else None,
            }
        )
    return top


def _build_discussion(record: SourceRecord, top_replies: list[dict]) -> str:
    lines = []
    if record.context_content.get("post_content"):
        lines.append(f"【原帖】{record.context_content['post_content']}\n")
    if top_replies:
        lines.append(f"【热门讨论】（共 {record.metrics.get('replies', 0)} 条回复，精选 {len(top_replies)} 条）\n")
        for reply in top_replies:
            thanked = reply.get("thanked", 0)
            prefix = f"👍{thanked} " if thanked else ""
            lines.append(f"{prefix}@{reply['author']}: {reply['text']}\n")
    return "\n".join(lines)


@register_adapter
class V2EXAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="community_v2ex",
        label="V2EX",
        group="Community",
        default_source_type="community",
        default_item_type="discussion",
        input_schema_version=1,
        input_schema=input_schema(
            source={"source_tag": STRING},
            fetch={
                "top_n": INTEGER,
                "top_clicked_limit": INTEGER,
                "max_replies_to_fetch": INTEGER,
                "max_replies_to_keep": INTEGER,
            },
            enrich_names=["top_replies"],
        ),
        default_input=default_input(
            fetch={"top_n": 10, "top_clicked_limit": 10, "max_replies_to_fetch": 30, "max_replies_to_keep": 10},
            enrich=[{"name": "top_replies", "when": "always"}],
        ),
        supported_enrichers=["top_replies"],
        description="抓取 V2EX 热门技术讨论。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        top_n = int(config.input.fetch.get("top_n") or 10)
        source_tag = str(config.input.source.get("source_tag") or "dev_community")
        resp = http_get(V2EX_HOT_API, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return []
        topics = resp.json()
        if not isinstance(topics, list):
            return []

        records: list[SourceRecord] = []
        for topic in topics[:top_n]:
            topic_id = topic.get("id")
            if not topic_id:
                continue
            author = topic.get("member", {}).get("username", "")
            post_content = _clean_html(topic.get("content_rendered") or topic.get("content") or "")
            url = topic.get("url") or f"https://www.v2ex.com/t/{topic_id}"
            records.append(
                SourceRecord(
                    identity=f"v2ex:{topic_id}",
                    url=url,
                    title=topic.get("title", ""),
                    content=post_content,
                    metrics={"replies": topic.get("replies", 0), "clicks": 0},
                    extra={
                        "topic_id": topic_id,
                        "node": topic.get("node", {}).get("title", ""),
                        "rank_type": "top_clicked",
                        "source_tag": source_tag,
                    },
                    context_content={"post_content": post_content, "top_comments": []},
                    author_id=author,
                    author_url=f"https://www.v2ex.com/member/{author}" if author else "",
                    source_published_date=datetime.fromtimestamp(topic.get("created", 0), tz=timezone.utc)
                    if topic.get("created")
                    else None,
                )
            )
        return records

    def enrich(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        fetch = config.input.fetch
        max_fetch = int(fetch.get("max_replies_to_fetch") or 30)
        max_keep = int(fetch.get("max_replies_to_keep") or 10)
        fetch_replies = enrich_enabled(config, "top_replies")
        for record in records:
            try:
                page = http_get(record.url, headers=HEADERS, timeout=10)
                match = re.search(r"(\d+)\s*次点击", page.text) if page.status_code == 200 else None
                record.metrics["clicks"] = int(match.group(1)) if match else 0
            except Exception:
                record.metrics["clicks"] = 0
            if not fetch_replies:
                continue
            topic_id = record.extra.get("topic_id")
            if not topic_id:
                continue
            top_replies = _top_replies(_fetch_replies(int(topic_id), max_fetch), max_keep)
            record.context_content["top_comments"] = top_replies
            record.context_content["top_comments_basis"] = "reply_thanked"
            record.content = _build_discussion(record, top_replies)
        return records

    def select(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        limit = int(config.input.fetch.get("top_clicked_limit") or 10)
        ranked = sorted(
            records,
            key=lambda record: (
                int(record.metrics.get("clicks") or 0),
                int(record.metrics.get("replies") or 0),
                record.source_published_date or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        for rank, record in enumerate(ranked[:limit], start=1):
            record.extra["rank"] = rank
        return ranked[:limit]

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
