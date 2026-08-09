from __future__ import annotations

import re
import threading
from datetime import datetime, timezone, timedelta

from bs4 import BeautifulSoup

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord, enrich_enabled
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get
from infra.gateways.jina_reader import fetch_jina_text

from .spec_helpers import BOOLEAN, INTEGER, JSON_OBJECT, STRING, default_input, input_schema

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
        match = regex.search(text)
        if match:
            try:
                return fmt_fn(match)
            except Exception:
                pass
    return None


def _fetch_full_text(url: str, timeout: int, max_chars: int, provider: str = "trafilatura") -> str:
    if provider == "jina":
        try:
            return fetch_jina_text(url, timeout=timeout)[:max_chars]
        except Exception:
            return ""

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


@register_adapter
class AIBlogAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="ai_blog",
        label="AI Blog / News",
        group="Website",
        default_source_type="website",
        default_item_type="article",
        input_schema_version=1,
        input_schema=input_schema(
            source={
                "base_url": STRING,
                "news_url": STRING,
                "link_selector": STRING,
                "author": STRING,
                "source_tag": STRING,
                "metadata": JSON_OBJECT,
            },
            fetch={
                "fetch_window_hours": INTEGER,
                "fetch_full_text": BOOLEAN,
                "full_text_timeout": INTEGER,
                "max_content_chars": INTEGER,
                "full_text_provider": STRING,
            },
            enrich_names=["full_text"],
        ),
        default_input=default_input(
            source={
                "base_url": "",
                "news_url": "",
                "link_selector": "a[href*='/news/']",
                "source_tag": "official_ai",
            },
            fetch={
                "fetch_window_hours": 0,
                "fetch_full_text": True,
                "full_text_timeout": 15,
                "max_content_chars": 12000,
                "full_text_provider": "trafilatura",
            },
            enrich=[{"name": "full_text", "when": "always"}],
        ),
        supported_enrichers=["full_text"],
        description="抓取官网/博客新闻列表，并可二阶段补全文。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        source = config.input.source
        fetch = config.input.fetch
        base_url = str(source.get("base_url") or "")
        news_url = str(source.get("news_url") or "")
        link_selector = str(source.get("link_selector") or "a[href*='/news/']")
        author = str(source.get("author") or "")
        source_tag = str(source.get("source_tag") or "official_ai")
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        max_content_chars = int(fetch.get("max_content_chars") or 12000)
        fetch_window = int(fetch.get("fetch_window_hours") or 0)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=fetch_window) if fetch_window else None

        if not news_url:
            return []
        res = http_get(news_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        records: list[SourceRecord] = []
        seen: set[str] = set()
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
            if cutoff and (published_at is None or published_at < cutoff):
                continue

            desc_tag = card.select_one("p")
            list_summary = desc_tag.get_text(strip=True) if desc_tag else ""
            extra = {"source_tag": source_tag}
            extra.update(metadata)
            records.append(
                SourceRecord(
                    identity=full_url,
                    url=full_url,
                    title=title,
                    content=list_summary[:max_content_chars],
                    extra=extra,
                    context_content={"list_summary": list_summary, "full_text_fetched": False},
                    author_id=author,
                    author_url=base_url,
                    source_published_date=published_at,
                )
            )
        return records

    def enrich(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        fetch = config.input.fetch
        if not enrich_enabled(config, "full_text") or not bool(fetch.get("fetch_full_text", True)):
            return records
        full_text_timeout = int(fetch.get("full_text_timeout") or 15)
        max_content_chars = int(fetch.get("max_content_chars") or 12000)
        full_text_provider = str(fetch.get("full_text_provider") or "trafilatura").lower()
        if full_text_provider not in {"jina", "trafilatura"}:
            full_text_provider = "trafilatura"
        for record in records:
            full_text = _fetch_full_text(record.url, full_text_timeout, max_content_chars, full_text_provider)
            if full_text:
                record.content = full_text[:max_content_chars]
                record.context_content["full_text_source"] = full_text_provider
            record.context_content["full_text_fetched"] = bool(full_text)
        return records

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
