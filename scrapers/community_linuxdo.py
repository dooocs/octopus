# scrapers/community_linuxdo.py

import re
import requests
from datetime import datetime, timezone
from infra.http import http_get
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

LINUXDO_BASE = "https://linux.do"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _clean_text(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text or '').strip()
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _enrich_enabled(config: dict, name: str, default: bool = True) -> bool:
    enrich = config.get("enrich")
    if not isinstance(enrich, list):
        return default
    return any(isinstance(item, dict) and item.get("name") == name for item in enrich)


@register("community_linuxdo")
class LinuxDoEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        return self.enrich_items(self.discover_items())

    def discover_items(self) -> list[RawItem]:
        top_n = self.config.get("top_n", 10)
        limit = self.config.get("limit", 10)
        source_tag = self.config.get("source_tag", "dev_community")

        try:
            resp = http_get(f"{LINUXDO_BASE}/top.json?period=daily", headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                return []
            topics = resp.json().get("topic_list", {}).get("topics", [])
        except Exception as e:
            print(f"  ⚠️ linux.do API 失败: {e}")
            return []

        if not topics:
            return []

        enriched = []
        for t in topics[:top_n]:
            topic_id = t["id"]
            slug = t.get("slug", "")
            topic_url = f"{LINUXDO_BASE}/t/{slug}/{topic_id}" if slug else f"{LINUXDO_BASE}/t/topic/{topic_id}"
            enriched.append({
                "id": topic_id, "title": t.get("title", ""), "url": topic_url,
                "posts_count": max(t.get("posts_count", 1) - 1, 0),
                "views": t.get("views", 0), "like_count": t.get("like_count", 0),
                "post_content": "",
                "top_comments": [],
                "created_at": t.get("created_at", ""),
            })

        ranked = sorted(enriched, key=lambda x: (x["posts_count"], x["views"], x["like_count"]), reverse=True)[:limit]
        items = [self._build_item(topic, rank, source_tag) for rank, topic in enumerate(ranked, start=1)]

        print(f"  [{self.name}] 返回 {len(items)} 条")
        return items

    def enrich_items(self, items: list[RawItem]) -> list[RawItem]:
        if not _enrich_enabled(self.config, "top_replies"):
            return items
        max_replies = self.config.get("max_replies_to_fetch", 30)
        max_keep = self.config.get("max_replies_to_keep", 10)
        for item in items:
            topic_id = item.extra.get("topic_id")
            if not topic_id:
                continue
            context = self._fetch_topic_context(int(topic_id), max_replies, max_keep)
            item.context_content["post_content"] = context["post_content"]
            item.context_content["top_comments"] = context["top_comments"]
            item.context_content["top_comments_basis"] = "comment_likes_score"
            item.extra["top_replies_count"] = len(context["top_comments"])
            item.body_text = self._discussion_text(context)
        return items

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
            for post in posts[1:max_replies + 1]:
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

    def _build_item(self, t: dict, rank: int, source_tag: str) -> RawItem:
        published_at = None
        if t.get("created_at"):
            try:
                published_at = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
            except Exception:
                pass

        return RawItem(
            title=t["title"], original_url=t["url"],
            source_name=self.name, source_type=self.config.get("source_type", "ARTICLE"),
            content_type=self.config.get("content_type", "linuxdo_hot"),
            author="", author_url="",
            body_text="",
            raw_metrics={"replies": t["posts_count"], "views": t["views"], "likes": t["like_count"]},
            extra={"source_tag": source_tag, "rank_type": "top_comments", "rank": rank, "top_replies_count": 0, "topic_id": t["id"]},
            context_content={
                "post_content": "",
                "top_comments": [],
            },
            published_at=published_at,
        )

    def _discussion_text(self, context: dict) -> str:
        body_parts = []
        if context.get("post_content"):
            body_parts.append(f"【原帖】\n{context['post_content']}")
        comments = context.get("top_comments") or []
        if comments:
            lines = [
                f"[回复{i+1}] {r['text'][:200]}{'...' if len(r['text']) > 200 else ''}"
                for i, r in enumerate(comments)
            ]
            body_parts.append(f"【热门讨论】\n" + "\n".join(lines))
        return "\n\n".join(body_parts)[:6000]
