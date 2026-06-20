# scrapers/hackernews.py

import html
import re
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from infra.http import http_get
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

HN_API = "https://hacker-news.firebaseio.com/v0"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

try:
    import trafilatura
    _trafilatura_lock = threading.Lock()
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False


def _fetch_body(url: str, skip_domains: list[str]) -> str:
    if not HAS_TRAFILATURA:
        return ""
    if any(d in url for d in skip_domains):
        return ""
    if "news.ycombinator.com" in url:
        return ""
    try:
        resp = http_get(url, timeout=10, headers=HEADERS)
        if resp.status_code != 200:
            return ""
        with _trafilatura_lock:
            content = trafilatura.extract(resp.text, include_comments=False, include_tables=True, no_fallback=False)
        return content or ""
    except Exception:
        return ""


def _clean_html(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<p\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _fetch_hn_item(item_id: int) -> dict:
    response = http_get(f"{HN_API}/item/{item_id}.json", timeout=10)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def _fetch_top_comments(story_id: int, max_fetch: int, max_keep: int) -> list[dict]:
    if max_keep <= 0:
        return []
    try:
        story = _fetch_hn_item(story_id)
    except Exception:
        return []
    comments: list[dict] = []
    for comment_id in (story.get("kids") or [])[:max_fetch]:
        try:
            comment = _fetch_hn_item(int(comment_id))
        except Exception:
            continue
        if not comment or comment.get("deleted") or comment.get("dead"):
            continue
        text = _clean_html(comment.get("text", ""))
        if not text:
            continue
        timestamp = comment.get("time")
        comments.append(
            {
                "id": comment.get("id"),
                "author": comment.get("by", ""),
                "text": text[:1000],
                "created_at": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() if timestamp else None,
                "reply_count": len(comment.get("kids") or []),
            }
        )
        if len(comments) >= max_keep:
            break
    return comments


def _enrich_enabled(config: dict, name: str, default: bool = True) -> bool:
    enrich = config.get("enrich")
    if not isinstance(enrich, list):
        return default
    return any(isinstance(item, dict) and item.get("name") == name for item in enrich)


@register("hackernews")
class HackerNewsEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        return self.enrich_items(self.discover_items())

    def discover_items(self) -> list[RawItem]:
        new_n = self.config.get("new_n", 500)
        min_score = self.config.get("min_score", 50)
        cutoff_hours = self.config.get("cutoff_hours", 36)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)
        seen = set()
        items = []

        try:
            resp = http_get(f"{HN_API}/newstories.json", timeout=15)
            resp.raise_for_status()
            story_ids = resp.json()[:new_n]

            for story_id in story_ids:
                result = self._fetch_story(story_id, seen, cutoff, min_score)
                if result is False:
                    break
                if result:
                    items.append(result)
        except Exception as e:
            print(f"⚠️ HN New Stories 失败: {e}")

        print(f"  共抓取 {len(items)} 条（score >= {min_score}，过去{cutoff_hours}小时）")
        return items

    def enrich_items(self, items: list[RawItem]) -> list[RawItem]:
        if not items:
            return items
        fetch_workers = self.config.get("fetch_workers", 5)
        skip_domains = self.config.get("skip_domains", ["twitter.com", "x.com", "medium.com", "zhihu.com"])
        max_comments_to_fetch = self.config.get("max_comments_to_fetch", 30)
        max_comments_to_keep = self.config.get("max_comments_to_keep", 10)
        fetch_article_body = _enrich_enabled(self.config, "article_body")
        fetch_top_comments = _enrich_enabled(self.config, "top_comments")
        if not fetch_article_body and not fetch_top_comments:
            return items
        print(f"  📄 并发补充 {len(items)} 篇正文和评论（workers={fetch_workers}）...")
        self._enrich_items(
            items,
            fetch_workers,
            skip_domains,
            max_comments_to_fetch,
            max_comments_to_keep,
            fetch_article_body=fetch_article_body,
            fetch_top_comments=fetch_top_comments,
        )
        return items

    def _enrich_items(
        self,
        items: list[RawItem],
        workers: int,
        skip_domains: list[str],
        max_comments_to_fetch: int,
        max_comments_to_keep: int,
        *,
        fetch_article_body: bool,
        fetch_top_comments: bool,
    ):
        def _enrich_one(item: RawItem) -> tuple[str, list[dict]]:
            story_id = item.raw_metrics.get("hn_id")
            article_body = _fetch_body(item.original_url, skip_domains) if fetch_article_body else ""
            comments = (
                _fetch_top_comments(int(story_id), max_comments_to_fetch, max_comments_to_keep)
                if fetch_top_comments and story_id
                else []
            )
            return article_body, comments

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_enrich_one, item): item for item in items}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    body, comments = future.result()
                    if body:
                        item.context_content["original_content"] = body[:8000]
                        item.body_text = body
                    if comments:
                        item.context_content["top_comments"] = comments
                        item.context_content["top_comments_basis"] = "hackernews_api_order"
                except Exception:
                    pass

    def _fetch_story(self, story_id: int, seen: set, cutoff: datetime, min_score: int):
        try:
            story = _fetch_hn_item(story_id)
            if not story or story.get("type") != "story":
                return None
            if story.get("dead") or story.get("deleted"):
                return None
            timestamp = story.get("time")
            if not timestamp:
                return None
            published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if published_at < cutoff:
                return False
            if story.get("score", 0) < min_score:
                return None
            title = story.get("title", "").strip()
            if not title:
                return None
            hn_page = f"https://news.ycombinator.com/item?id={story_id}"
            url = story.get("url") or hn_page
            if url in seen:
                return None
            seen.add(url)
            author = story.get("by", "")
            post_text = _clean_html(story.get("text", ""))
            return RawItem(
                title=title,
                original_url=url,
                source_name=self.name,
                source_type=self.config.get("source_type", "NEWS"),
                content_type=self.config.get("content_type", "article"),
                author=author,
                author_url=f"https://news.ycombinator.com/user?id={author}" if author else "",
                body_text=post_text,
                raw_metrics={"score": story.get("score", 0), "comments": story.get("descendants", 0), "hn_id": story_id, "hn_url": hn_page},
                context_content={
                    "hn_post_text": post_text,
                    "hn_url": hn_page,
                    "external_url": story.get("url") or "",
                },
                published_at=published_at,
            )
        except Exception:
            return None
