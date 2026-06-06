# scrapers/rss_feed.py

import re
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


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


@register("rss")
class RSSFeedEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        url = self.config.get("url", "")
        if not url:
            print(f"  ⚠️ [{self.name}] 无 url，跳过")
            return []

        max_items = self.config.get("max_items")
        fetch_window = self.config.get("fetch_window_hours", 25)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=fetch_window)
        source_type = self.config.get("source_type", "ARTICLE")
        content_type = self.config.get("content_type", "article")

        try:
            resp = requests.get(url, timeout=15, headers=HEADERS)
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

                body_text = _clean_text(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
                published_at = _parse_date(entry)

                if published_at is not None and published_at < cutoff:
                    skipped_old += 1
                    break

                items.append(RawItem(
                    title=title,
                    original_url=entry_url,
                    source_name=self.name,
                    source_type=source_type,
                    content_type=content_type,
                    author=(getattr(entry, "author", "") or "").strip(),
                    body_text=body_text[:1000],
                    raw_metrics={},
                    extra={"source_tag": self.config.get("source_tag", ""), "feed_url": url},
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
