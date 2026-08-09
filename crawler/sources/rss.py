from __future__ import annotations

import re
import threading
from datetime import datetime, timezone, timedelta
from typing import Any

import feedparser

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord, enrich_enabled
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get
from infra.gateways.jina_reader import fetch_jina_text

from .spec_helpers import BOOLEAN, INTEGER, JSON_OBJECT, STRING, default_input, input_schema

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


def _parse_date(entry: Any) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_retweet(title: str) -> bool:
    return title.strip().startswith("RT by @")


def _fetch_full_text(url: str, timeout: int, max_chars: int, provider: str = "trafilatura") -> str:
    if provider == "jina":
        try:
            return fetch_jina_text(url, timeout=timeout)[:max_chars]
        except Exception:
            return ""

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


@register_adapter
class RSSAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="rss",
        label="RSS Feed",
        group="Website",
        default_source_type="website",
        default_item_type="article",
        input_schema_version=1,
        input_schema=input_schema(
            source={"url": STRING, "source_tag": STRING, "metadata": JSON_OBJECT},
            fetch={
                "max_items": INTEGER,
                "fetch_window_hours": INTEGER,
                "fetch_full_text": BOOLEAN,
                "full_text_timeout": INTEGER,
                "max_content_chars": INTEGER,
                "full_text_provider": STRING,
            },
            enrich_names=["full_text"],
        ),
        default_input=default_input(
            source={"url": ""},
            fetch={
                "max_items": 10,
                "fetch_window_hours": 25,
                "fetch_full_text": True,
                "full_text_timeout": 15,
                "max_content_chars": 12000,
                "full_text_provider": "trafilatura",
            },
            enrich=[{"name": "full_text", "when": "always"}],
        ),
        supported_enrichers=["full_text"],
        description="抓取标准 RSS/Atom 订阅源。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        source = config.input.source
        fetch = config.input.fetch
        url = str(source.get("url") or "")
        if not url:
            return []
        max_items = fetch.get("max_items")
        fetch_window = int(fetch.get("fetch_window_hours") or 25)
        max_content_chars = int(fetch.get("max_content_chars") or 12000)
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        cutoff = datetime.now(timezone.utc) - timedelta(hours=fetch_window)

        resp = http_get(url, timeout=15, headers=HEADERS)
        if resp.status_code != 200:
            return []
        parsed = feedparser.parse(resp.text)
        if parsed.bozo and not parsed.entries:
            return []

        records: list[SourceRecord] = []
        for entry in parsed.entries:
            title = (getattr(entry, "title", "") or "").strip()
            if _is_retweet(title):
                continue
            entry_url = (getattr(entry, "link", "") or "").strip()
            if not title or not entry_url:
                continue
            published_at = _parse_date(entry)
            if published_at is not None and published_at < cutoff:
                break
            feed_summary = _clean_text(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
            extra = {"source_tag": source.get("source_tag", ""), "feed_url": url}
            extra.update(metadata)
            records.append(
                SourceRecord(
                    identity=entry_url,
                    url=entry_url,
                    title=title,
                    content=feed_summary[:max_content_chars],
                    extra=extra,
                    context_content={"feed_summary": feed_summary, "full_text_fetched": False},
                    author_id=(getattr(entry, "author", "") or "").strip(),
                    source_published_date=published_at,
                )
            )
            if max_items is not None and len(records) >= int(max_items):
                break
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
            body_text=record.content,
            raw_metrics=record.metrics,
            extra=record.extra,
            context_content=record.context_content,
            published_at=record.source_published_date,
        )
