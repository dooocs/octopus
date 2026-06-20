from __future__ import annotations

import re
from datetime import datetime, timezone

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord, enrich_enabled
from core.registry import register_adapter
from infra.http import http_get

from .spec_helpers import INTEGER, STRING, default_input, input_schema

LINUXDO_BASE = "https://linux.do"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@register_adapter
class LinuxDoAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="community_linuxdo",
        label="LinuxDo",
        group="Community",
        default_source_type="community",
        default_item_type="discussion",
        input_schema_version=1,
        input_schema=input_schema(
            source={"source_tag": STRING},
            fetch={"top_n": INTEGER, "limit": INTEGER, "max_replies_to_fetch": INTEGER, "max_replies_to_keep": INTEGER},
            enrich_names=["top_replies"],
        ),
        default_input=default_input(
            fetch={"top_n": 10, "limit": 10, "max_replies_to_fetch": 30, "max_replies_to_keep": 10},
            enrich=[{"name": "top_replies", "when": "always"}],
        ),
        supported_enrichers=["top_replies"],
        description="抓取 LinuxDo 热门讨论。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        top_n = int(config.input.fetch.get("top_n") or 10)
        source_tag = str(config.input.source.get("source_tag") or "dev_community")
        resp = http_get(f"{LINUXDO_BASE}/top.json?period=daily", headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return []
        topics = resp.json().get("topic_list", {}).get("topics", [])
        if not isinstance(topics, list):
            return []

        records: list[SourceRecord] = []
        for topic in topics[:top_n]:
            topic_id = topic.get("id")
            if not topic_id:
                continue
            slug = topic.get("slug", "")
            url = f"{LINUXDO_BASE}/t/{slug}/{topic_id}" if slug else f"{LINUXDO_BASE}/t/topic/{topic_id}"
            published_at = None
            if topic.get("created_at"):
                try:
                    published_at = datetime.fromisoformat(topic["created_at"].replace("Z", "+00:00"))
                except Exception:
                    pass
            records.append(
                SourceRecord(
                    identity=f"linuxdo:{topic_id}",
                    url=url,
                    title=topic.get("title", ""),
                    content="",
                    metrics={
                        "replies": max(topic.get("posts_count", 1) - 1, 0),
                        "views": topic.get("views", 0),
                        "likes": topic.get("like_count", 0),
                    },
                    extra={"source_tag": source_tag, "rank_type": "top_comments", "top_replies_count": 0, "topic_id": topic_id},
                    context_content={"post_content": "", "top_comments": []},
                    source_published_date=published_at,
                )
            )
        return records

    def enrich(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        if not enrich_enabled(config, "top_replies"):
            return records
        max_replies = int(config.input.fetch.get("max_replies_to_fetch") or 30)
        max_keep = int(config.input.fetch.get("max_replies_to_keep") or 10)
        for record in records:
            context = self._fetch_topic_context(int(record.extra.get("topic_id") or 0), max_replies, max_keep)
            record.context_content["post_content"] = context["post_content"]
            record.context_content["top_comments"] = context["top_comments"]
            record.context_content["top_comments_basis"] = "comment_likes_score"
            record.extra["top_replies_count"] = len(context["top_comments"])
            record.content = self._discussion_text(context)
        return records

    def select(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        limit = int(config.input.fetch.get("limit") or 10)
        ranked = sorted(
            records,
            key=lambda record: (
                int(record.metrics.get("replies") or 0),
                int(record.metrics.get("views") or 0),
                int(record.metrics.get("likes") or 0),
            ),
            reverse=True,
        )
        for rank, record in enumerate(ranked[:limit], start=1):
            record.extra["rank"] = rank
        return ranked[:limit]

    def _fetch_topic_context(self, topic_id: int, max_replies: int, max_keep: int) -> dict:
        try:
            resp = http_get(f"{LINUXDO_BASE}/t/{topic_id}.json", headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                return {"post_content": "", "top_comments": []}
            posts = resp.json().get("post_stream", {}).get("posts", [])
            post_content = ""
            if posts:
                post_content = posts[0].get("raw") or _clean_text(posts[0].get("cooked", ""))
            comments = []
            for post in posts[1 : max_replies + 1]:
                text = post.get("raw") or _clean_text(post.get("cooked", ""))
                if text:
                    comments.append(
                        {
                            "id": post.get("id"),
                            "author": post.get("username") or post.get("name") or "",
                            "text": text[:1000],
                            "likes": post.get("like_count", 0),
                            "score": post.get("score", 0),
                            "created_at": post.get("created_at"),
                        }
                    )
            comments = sorted(comments, key=lambda item: (item.get("likes") or 0, item.get("score") or 0), reverse=True)[:max_keep]
            return {"post_content": post_content[:4000], "top_comments": comments}
        except Exception:
            return {"post_content": "", "top_comments": []}

    def _discussion_text(self, context: dict) -> str:
        body_parts = []
        if context.get("post_content"):
            body_parts.append(f"【原帖】\n{context['post_content']}")
        comments = context.get("top_comments") or []
        if comments:
            lines = [
                f"[回复{i + 1}] {reply['text'][:200]}{'...' if len(reply['text']) > 200 else ''}"
                for i, reply in enumerate(comments)
            ]
            body_parts.append("【热门讨论】\n" + "\n".join(lines))
        return "\n\n".join(body_parts)[:6000]

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
