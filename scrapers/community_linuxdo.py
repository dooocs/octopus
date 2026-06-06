# scrapers/community_linuxdo.py

import re
import requests
from datetime import datetime, timezone
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

LINUXDO_BASE = "https://linux.do"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


@register("community_linuxdo")
class LinuxDoEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        top_n = self.config.get("top_n", 10)
        max_replies = self.config.get("max_replies_to_fetch", 30)
        source_tag = self.config.get("source_tag", "dev_community")

        try:
            resp = requests.get(f"{LINUXDO_BASE}/top.json?period=daily", headers=HEADERS, timeout=15)
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
            replies_text = self._fetch_replies(topic_id, max_replies)
            enriched.append({
                "id": topic_id, "title": t.get("title", ""), "url": topic_url,
                "posts_count": max(t.get("posts_count", 1) - 1, 0),
                "views": t.get("views", 0), "like_count": t.get("like_count", 0),
                "replies_text": replies_text, "created_at": t.get("created_at", ""),
            })

        top_replies = max(enriched, key=lambda x: x["posts_count"])
        top_views = max(enriched, key=lambda x: x["views"])

        items = []
        if top_replies["id"] == top_views["id"]:
            items.append(self._build_item(top_replies, "最热+最多评论", source_tag))
        else:
            items.append(self._build_item(top_replies, "最多评论", source_tag))
            items.append(self._build_item(top_views, "最多点击", source_tag))

        print(f"  [{self.name}] 返回 {len(items)} 条")
        return items

    def _fetch_replies(self, topic_id: int, max_replies: int) -> list[str]:
        try:
            resp = requests.get(f"{LINUXDO_BASE}/t/{topic_id}.json", headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                return []
            posts = resp.json().get("post_stream", {}).get("posts", [])
            replies = []
            for post in posts[1:max_replies + 1]:
                text = post.get("raw", "")
                if not text:
                    cooked = post.get("cooked", "")
                    text = re.sub(r'<[^>]+>', '', cooked).strip()
                    text = re.sub(r'\s+', ' ', text)
                if text:
                    replies.append(text[:300])
            return replies
        except Exception:
            return []

    def _build_item(self, t: dict, rank_type: str, source_tag: str) -> RawItem:
        body_parts = []
        if t["replies_text"]:
            lines = [f"[回复{i+1}] {r[:200]}{'...' if len(r) > 200 else ''}" for i, r in enumerate(t["replies_text"])]
            body_parts.append(f"【热门讨论】\n" + "\n".join(lines))

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
            body_text="\n".join(body_parts)[:2000],
            raw_metrics={"replies": t["posts_count"], "views": t["views"], "likes": t["like_count"]},
            extra={"source_tag": source_tag, "rank_type": rank_type, "top_replies_count": len(t["replies_text"])},
            published_at=published_at,
        )
