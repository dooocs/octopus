# scrapers/ai_blog.py
# 通用 AI 博客 HTML 抓取引擎

import re
import requests
import threading
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from infra.http import http_get
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

_CJK_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_EN_SHORT_RE = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2}),?\s+(\d{4})")
_EN_FULL_RE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})")

try:
    import trafilatura
    _trafilatura_lock = threading.Lock()
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False


def _extract_date_from_text(text: str) -> datetime | None:
    if not text:
        return None
    for regex, fmt_fn in [
        (_CJK_DATE_RE, lambda m: datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)),
        (_ISO_DATE_RE, lambda m: datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)),
        (_EN_FULL_RE, lambda m: datetime.strptime(f"{m.group(1)} {m.group(2)}, {m.group(3)}", "%B %d, %Y").replace(tzinfo=timezone.utc)),
        (_EN_SHORT_RE, lambda m: datetime.strptime(f"{m.group(1)} {m.group(2)}, {m.group(3)}", "%b %d, %Y").replace(tzinfo=timezone.utc)),
    ]:
        m = regex.search(text)
        if m:
            try:
                return fmt_fn(m)
            except Exception:
                pass
    return None


def _fetch_full_text(url: str, timeout: int, max_chars: int) -> str:
    if not HAS_TRAFILATURA:
        return ""
    try:
        res = http_get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        if res.status_code != 200:
            return ""
        with _trafilatura_lock:
            content = trafilatura.extract(res.text, include_comments=False, include_tables=True, no_fallback=False)
        return (content or "").strip()[:max_chars]
    except Exception:
        return ""


def _enrich_enabled(config: dict, name: str, default: bool = True) -> bool:
    enrich = config.get("enrich")
    if not isinstance(enrich, list):
        return default
    return any(isinstance(item, dict) and item.get("name") == name for item in enrich)


@register("ai_blog")
class AIBlogEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        return self.enrich_items(self.discover_items())

    def discover_items(self) -> list[RawItem]:
        base_url = self.config.get("base_url", "")
        news_url = self.config.get("news_url", "")
        link_selector = self.config.get("link_selector", "a[href*='/news/']")
        author = self.config.get("author", "")
        source_tag = self.config.get("source_tag", "official_ai")
        fetch_window = self.config.get("fetch_window_hours", 0)
        metadata = self.config.get("metadata") if isinstance(self.config.get("metadata"), dict) else {}
        max_content_chars = int(self.config.get("max_content_chars", 12000))

        cutoff = None
        if fetch_window:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=fetch_window)

        try:
            res = http_get(news_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")

            items = []
            seen = set()

            for card in soup.select(link_selector):
                href = card.get("href", "")
                if not href or href in seen:
                    continue
                path_parts = href.rstrip("/").split("/")
                if len(path_parts) <= 1:
                    continue
                seen.add(href)

                full_url = href if href.startswith("http") else base_url + href
                title_tag = card.select_one("h2, h3, h4")
                title = title_tag.get_text(strip=True) if title_tag else card.get_text(strip=True)
                if not title:
                    continue

                published_at = None
                time_tag = card.select_one("time")
                if time_tag:
                    dt_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
                    try:
                        published_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    except Exception:
                        published_at = _extract_date_from_text(dt_str)

                if published_at is None:
                    for sel in ("div.body-3.agate", "[class*='date']", "[class*='time']", "[class*='agate']"):
                        el = card.select_one(sel)
                        if el:
                            published_at = _extract_date_from_text(el.get_text(strip=True))
                            if published_at:
                                break

                if published_at is None:
                    published_at = _extract_date_from_text(card.get_text(" ", strip=True))

                if cutoff and published_at is not None and published_at < cutoff:
                    continue
                if cutoff and published_at is None:
                    continue

                desc_tag = card.select_one("p")
                list_summary = desc_tag.get_text(strip=True) if desc_tag else ""
                extra = {"source_tag": source_tag}
                extra.update(metadata)

                items.append(RawItem(
                    title=title,
                    original_url=full_url,
                    source_name=self.name,
                    source_type=self.config.get("source_type", "BLOG"),
                    content_type=self.config.get("content_type", "article"),
                    author=author,
                    author_url=base_url,
                    body_text=list_summary[:max_content_chars],
                    raw_metrics={},
                    extra=extra,
                    context_content={
                        "list_summary": list_summary,
                        "full_text_fetched": False,
                    },
                    published_at=published_at,
                ))

            print(f"  [{self.name}] 抓取到 {len(items)} 条")
            return items
        except Exception as e:
            print(f"⚠️ {self.name} 抓取失败: {e}")
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
