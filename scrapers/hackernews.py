# scrapers/hackernews.py

import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
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
        resp = requests.get(url, timeout=10, headers=HEADERS)
        if resp.status_code != 200:
            return ""
        with _trafilatura_lock:
            content = trafilatura.extract(resp.text, include_comments=False, include_tables=True, no_fallback=False)
        return content or ""
    except Exception:
        return ""


@register("hackernews")
class HackerNewsEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        new_n = self.config.get("new_n", 500)
        min_score = self.config.get("min_score", 50)
        cutoff_hours = self.config.get("cutoff_hours", 36)
        fetch_workers = self.config.get("fetch_workers", 5)
        skip_domains = self.config.get("skip_domains", ["twitter.com", "x.com", "medium.com", "zhihu.com"])

        cutoff = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)
        seen = set()
        items = []

        try:
            resp = requests.get(f"{HN_API}/newstories.json", timeout=15)
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

        if items:
            print(f"  📄 并发抓取 {len(items)} 篇正文（workers={fetch_workers}）...")
            self._enrich_items(items, fetch_workers, skip_domains)

        print(f"  共抓取 {len(items)} 条（score >= {min_score}，过去{cutoff_hours}小时）")
        return items

    def _enrich_items(self, items: list[RawItem], workers: int, skip_domains: list[str]):
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch_body, item.original_url, skip_domains): item for item in items}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    body = future.result()
                    if body:
                        item.body_text = body
                except Exception:
                    pass

    def _fetch_story(self, story_id: int, seen: set, cutoff: datetime, min_score: int):
        try:
            r = requests.get(f"{HN_API}/item/{story_id}.json", timeout=10)
            r.raise_for_status()
            story = r.json()
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
            return RawItem(
                title=title,
                original_url=url,
                source_name=self.name,
                source_type=self.config.get("source_type", "NEWS"),
                content_type=self.config.get("content_type", "article"),
                author=author,
                author_url=f"https://news.ycombinator.com/user?id={author}" if author else "",
                body_text="",
                raw_metrics={"score": story.get("score", 0), "comments": story.get("descendants", 0), "hn_id": story_id, "hn_url": hn_page},
                published_at=published_at,
            )
        except Exception:
            return None
