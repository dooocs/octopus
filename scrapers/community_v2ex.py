# scrapers/community_v2ex.py

import re
import requests
from datetime import datetime, timezone
from infra.http import http_get
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

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
    for r in sorted(replies, key=lambda item: item.get("thanked", 0), reverse=True)[:max_keep]:
        author = r.get("member", {}).get("username", "匿名")
        text = _clean_html(r.get("content_rendered") or r.get("content") or "")
        if not text:
            continue
        top.append(
            {
                "id": r.get("id"),
                "author": author,
                "text": text[:1000],
                "thanked": r.get("thanked", 0),
                "created_at": datetime.fromtimestamp(r.get("created", 0), tz=timezone.utc).isoformat() if r.get("created") else None,
            }
        )
    return top


def _build_discussion(topic: dict, top_replies: list[dict]) -> str:
    lines = []
    content = _clean_html(topic.get("content_rendered") or topic.get("content") or "")
    if content:
        lines.append(f"【原帖】{content}\n")
    if not top_replies:
        return "\n".join(lines)
    lines.append(f"【热门讨论】（共 {topic.get('replies', 0)} 条回复，精选 {len(top_replies)} 条）\n")
    for r in top_replies:
        thanked = r.get("thanked", 0)
        prefix = f"👍{thanked} " if thanked else ""
        lines.append(f"{prefix}@{r['author']}: {r['text']}\n")
    return "\n".join(lines)


def _enrich_enabled(config: dict, name: str, default: bool = True) -> bool:
    enrich = config.get("enrich")
    if not isinstance(enrich, list):
        return default
    return any(isinstance(item, dict) and item.get("name") == name for item in enrich)


@register("community_v2ex")
class V2EXEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        return self.enrich_items(self.discover_items())

    def discover_items(self) -> list[RawItem]:
        top_n = self.config.get("top_n", 10)
        top_clicked_limit = self.config.get("top_clicked_limit", 10)
        source_tag = self.config.get("source_tag", "dev_community")

        try:
            resp = http_get(V2EX_HOT_API, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                return []
            topics = resp.json()
        except Exception as e:
            print(f"  ⚠️ V2EX hot API 失败: {e}")
            return []

        if not topics:
            return []

        for i, t in enumerate(topics[:top_n]):
            url = t.get("url") or f"https://www.v2ex.com/t/{t['id']}"
            try:
                page = http_get(url, headers=HEADERS, timeout=10)
                match = re.search(r'(\d+)\s*次点击', page.text) if page.status_code == 200 else None
                t["_clicks"] = int(match.group(1)) if match else 0
            except Exception:
                t["_clicks"] = 0

        for t in topics[top_n:]:
            t["_clicks"] = 0

        results = []
        ranked_topics = sorted(
            topics[:top_n],
            key=lambda t: (t.get("_clicks", 0), t.get("replies", 0), t.get("created", 0)),
            reverse=True,
        )[:top_clicked_limit]

        def _build(topic, rank: int):
            author = topic.get("member", {}).get("username", "")
            post_content = _clean_html(topic.get("content_rendered") or topic.get("content") or "")
            return RawItem(
                title=topic.get("title", ""),
                original_url=topic.get("url") or f"https://www.v2ex.com/t/{topic['id']}",
                source_name=self.name,
                source_type=self.config.get("source_type", "ARTICLE"),
                content_type=self.config.get("content_type", "v2ex_hot"),
                author=author,
                author_url=f"https://www.v2ex.com/member/{author}" if author else "",
                body_text=post_content,
                raw_metrics={"replies": topic.get("replies", 0), "clicks": topic.get("_clicks", 0)},
                extra={
                    "topic_id": topic["id"],
                    "node": topic.get("node", {}).get("title", ""),
                    "rank_type": "top_clicked",
                    "rank": rank,
                    "source_tag": source_tag,
                },
                context_content={
                    "post_content": post_content,
                },
                published_at=datetime.fromtimestamp(topic.get("created", 0), tz=timezone.utc) if topic.get("created") else None,
            )

        for rank, topic in enumerate(ranked_topics, start=1):
            results.append(_build(topic, rank))

        print(f"  [{self.name}] 返回 {len(results)} 条")
        return results

    def enrich_items(self, items: list[RawItem]) -> list[RawItem]:
        if not _enrich_enabled(self.config, "top_replies"):
            return items
        max_fetch = self.config.get("max_replies_to_fetch", 30)
        max_keep = self.config.get("max_replies_to_keep", 10)
        for item in items:
            topic_id = item.extra.get("topic_id")
            if not topic_id:
                continue
            replies = _fetch_replies(int(topic_id), max_fetch)
            top_replies = _top_replies(replies, max_keep)
            topic = {
                "content_rendered": item.context_content.get("post_content", ""),
                "replies": item.raw_metrics.get("replies", 0),
            }
            item.body_text = _build_discussion(topic, top_replies)
            item.context_content["top_comments"] = top_replies
            item.context_content["top_comments_basis"] = "reply_thanked"
        return items
