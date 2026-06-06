# scrapers/community_v2ex.py

import re
import requests
from datetime import datetime, timezone
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
        resp = requests.get(V2EX_REPLIES_API, params={"topic_id": topic_id, "p": 1}, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        replies = resp.json()
        return replies[:max_fetch] if isinstance(replies, list) else []
    except Exception:
        return []


def _build_discussion(topic: dict, replies: list[dict], max_keep: int) -> str:
    lines = []
    content = _clean_html(topic.get("content_rendered") or topic.get("content") or "")
    if content:
        lines.append(f"【原帖】{content}\n")
    if not replies:
        return "\n".join(lines)
    sorted_replies = sorted(replies, key=lambda r: r.get("thanked", 0), reverse=True)[:max_keep]
    lines.append(f"【热门讨论】（共 {topic.get('replies', 0)} 条回复，精选 {len(sorted_replies)} 条）\n")
    for r in sorted_replies:
        author = r.get("member", {}).get("username", "匿名")
        text = _clean_html(r.get("content_rendered") or r.get("content") or "")
        if not text:
            continue
        thanked = r.get("thanked", 0)
        prefix = f"👍{thanked} " if thanked else ""
        lines.append(f"{prefix}@{author}: {text}\n")
    return "\n".join(lines)


@register("community_v2ex")
class V2EXEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        top_n = self.config.get("top_n", 10)
        max_fetch = self.config.get("max_replies_to_fetch", 30)
        max_keep = self.config.get("max_replies_to_keep", 15)
        source_tag = self.config.get("source_tag", "dev_community")

        try:
            resp = requests.get(V2EX_HOT_API, headers=HEADERS, timeout=15)
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
                page = requests.get(url, headers=HEADERS, timeout=10)
                match = re.search(r'(\d+)\s*次点击', page.text) if page.status_code == 200 else None
                t["_clicks"] = int(match.group(1)) if match else 0
            except Exception:
                t["_clicks"] = 0

        for t in topics[top_n:]:
            t["_clicks"] = 0

        most_replied = max(topics[:top_n], key=lambda t: t.get("replies", 0))
        most_clicked = max(topics[:top_n], key=lambda t: t.get("_clicks", 0))

        results = []

        def _build(topic, tag):
            replies = _fetch_replies(topic["id"], max_fetch)
            discussion = _build_discussion(topic, replies, max_keep)
            author = topic.get("member", {}).get("username", "")
            return RawItem(
                title=topic.get("title", ""),
                original_url=topic.get("url") or f"https://www.v2ex.com/t/{topic['id']}",
                source_name=self.name,
                source_type=self.config.get("source_type", "ARTICLE"),
                content_type=self.config.get("content_type", "v2ex_hot"),
                author=author,
                author_url=f"https://www.v2ex.com/member/{author}" if author else "",
                body_text=discussion,
                raw_metrics={"replies": topic.get("replies", 0), "clicks": topic.get("_clicks", 0)},
                extra={"topic_id": topic["id"], "node": topic.get("node", {}).get("title", ""), "hot_tag": tag, "source_tag": source_tag},
                published_at=datetime.fromtimestamp(topic.get("created", 0), tz=timezone.utc) if topic.get("created") else None,
            )

        if most_replied["id"] == most_clicked["id"]:
            results.append(_build(most_replied, "most_replied_and_clicked"))
        else:
            results.append(_build(most_replied, "most_replied"))
            results.append(_build(most_clicked, "most_clicked"))

        print(f"  [{self.name}] 返回 {len(results)} 条")
        return results
