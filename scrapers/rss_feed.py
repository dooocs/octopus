# scrapers/rss_feed.py

import re
import requests
import feedparser
import threading
from datetime import datetime, timezone, timedelta
from infra.http import http_get
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

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


def _parse_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_retweet(title: str) -> bool:
    return title.strip().startswith("RT by @")


def _fetch_full_text(url: str, timeout: int, max_chars: int) -> str:
    if not HAS_TRAFILATURA:
        return ""
    try:
        resp = http_get(url, timeout=timeout, headers=HEADERS)
        if resp.status_code != 200:
            return ""
        with _trafilatura_lock:
            content = trafilatura.extract(resp.text, include_comments=False, include_tables=True, no_fallback=False)
        return (content or "").strip()[:max_chars]
    except Exception:
        return ""


def _enrich_enabled(config: dict, name: str, default: bool = True) -> bool:
    enrich = config.get("enrich")
    if not isinstance(enrich, list):
        return default
    return any(isinstance(item, dict) and item.get("name") == name for item in enrich)


@register("rss")
class RSSFeedEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        return self.enrich_items(self.discover_items())

    def discover_items(self) -> list[RawItem]:
        url = self.config.get("url", "")
        if not url:
            print(f"  ⚠️ [{self.name}] 无 url，跳过")
            return []

        max_items = self.config.get("max_items")
        fetch_window = self.config.get("fetch_window_hours", 25)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=fetch_window)
        source_type = self.config.get("source_type", "ARTICLE")
        content_type = self.config.get("content_type", "article")
        metadata = self.config.get("metadata") if isinstance(self.config.get("metadata"), dict) else {}
        max_content_chars = int(self.config.get("max_content_chars", 12000))

        try:
            resp = http_get(url, timeout=15, headers=HEADERS)
            if resp.status_code != 200:
                print(f"  ⚠️ [{self.name}] HTTP {resp.status_code}")
                return []
            parsed = feedparser.parse(resp.text)

            if parsed.bozo and not parsed.entries:
                print(f"  ⚠️ [{self.name}] RSS 解析失败: {parsed.bozo_exception}")
                return []

            items = []
            skipped_old = 0

            for entry in parsed.entries:
                title = (getattr(entry, "title", "") or "").strip()
                if _is_retweet(title):
                    continue
                entry_url = (getattr(entry, "link", "") or "").strip()
                if not title or not entry_url:
                    continue

                feed_summary = _clean_text(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
                published_at = _parse_date(entry)

                if published_at is not None and published_at < cutoff:
                    skipped_old += 1
                    break

                extra = {"source_tag": self.config.get("source_tag", ""), "feed_url": url}
                extra.update(metadata)

                items.append(RawItem(
                    title=title,
                    original_url=entry_url,
                    source_name=self.name,
                    source_type=source_type,
                    content_type=content_type,
                    author=(getattr(entry, "author", "") or "").strip(),
                    body_text=feed_summary[:max_content_chars],
                    raw_metrics={},
                    extra=extra,
                    context_content={
                        "feed_summary": feed_summary,
                        "full_text_fetched": False,
                    },
                    published_at=published_at,
                ))

                if max_items is not None and len(items) >= max_items:
                    break

            log = f"  [{self.name}] {len(items)} 条"
            if skipped_old:
                log += "，遇到过期内容后停止"
            print(log)
            return items

        except Exception as e:
            print(f"  ❌ [{self.name}] 失败: {e}")
            return []

    def enrich_items(self, items: list[RawItem]) -> list[RawItem]:
        if not _enrich_enabled(self.config, "full_text", default=bool(self.config.get("fetch_full_text", True))):
            return items
        if not self.config.get("fetch_full_text", True):
            return items
        full_text_timeout = int(self.config.get("full_text_timeout", 15))
        max_content_chars = int(self.config.get("max_content_chars", 12000))
        for item in items:
            full_text = _fetch_full_text(item.original_url, full_text_timeout, max_content_chars)
            if full_text:
                item.body_text = full_text[:max_content_chars]
            item.context_content["full_text_fetched"] = bool(full_text)
        return items
