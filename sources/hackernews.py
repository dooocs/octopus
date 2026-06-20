from __future__ import annotations

import html
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord, enrich_enabled
from core.registry import register_adapter
from infra.http import http_get

from .spec_helpers import INTEGER, STRING, STRING_ARRAY, default_input, input_schema

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
    if any(domain in url for domain in skip_domains):
        return ""
    if "news.ycombinator.com" in url:
        return ""
    try:
        response = http_get(url, timeout=10, headers=HEADERS)
        if response.status_code != 200:
            return ""
        with _trafilatura_lock:
            content = trafilatura.extract(response.text, include_comments=False, include_tables=True, no_fallback=False)
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


@register_adapter
class HackerNewsAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="hackernews",
        label="HackerNews",
        group="Community",
        default_source_type="community",
        default_item_type="article",
        input_schema_version=1,
        input_schema=input_schema(
            source={"feed": STRING},
            fetch={
                "new_n": INTEGER,
                "cutoff_hours": INTEGER,
                "fetch_workers": INTEGER,
                "skip_domains": STRING_ARRAY,
                "max_comments_to_fetch": INTEGER,
                "max_comments_to_keep": INTEGER,
            },
            filters={"min_score": INTEGER},
            enrich_names=["article_body", "top_comments"],
        ),
        default_input=default_input(
            source={"feed": "newstories"},
            fetch={
                "new_n": 100,
                "cutoff_hours": 36,
                "fetch_workers": 5,
                "skip_domains": ["twitter.com", "x.com", "medium.com", "zhihu.com"],
                "max_comments_to_fetch": 30,
                "max_comments_to_keep": 10,
            },
            filters={"min_score": 50},
            enrich=[
                {"name": "article_body", "when": "has_external_url"},
                {"name": "top_comments", "when": "always"},
            ],
        ),
        supported_enrichers=["article_body", "top_comments"],
        description="抓取 Hacker News 高分新帖。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        new_n = int(config.input.fetch.get("new_n") or 500)
        cutoff_hours = int(config.input.fetch.get("cutoff_hours") or 36)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)

        resp = http_get(f"{HN_API}/newstories.json", timeout=15)
        resp.raise_for_status()
        story_ids = resp.json()[:new_n]
        seen: set[str] = set()
        records: list[SourceRecord] = []
        for story_id in story_ids:
            record = self._story_record(int(story_id), seen, cutoff)
            if record is False:
                break
            if record:
                records.append(record)
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        min_score = int(config.input.filters.get("min_score") or 50)
        return [record for record in records if int(record.metrics.get("score") or 0) >= min_score]

    def enrich(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        if not records:
            return records
        fetch = config.input.fetch
        workers = int(fetch.get("fetch_workers") or 5)
        skip_domains = [str(item) for item in fetch.get("skip_domains", ["twitter.com", "x.com", "medium.com", "zhihu.com"])]
        max_comments_to_fetch = int(fetch.get("max_comments_to_fetch") or 30)
        max_comments_to_keep = int(fetch.get("max_comments_to_keep") or 10)
        fetch_article_body = enrich_enabled(config, "article_body")
        fetch_top_comments = enrich_enabled(config, "top_comments")
        if not fetch_article_body and not fetch_top_comments:
            return records

        def enrich_one(record: SourceRecord) -> tuple[SourceRecord, str, list[dict]]:
            story_id = record.metrics.get("hn_id")
            article_body = _fetch_body(record.url, skip_domains) if fetch_article_body else ""
            comments = (
                _fetch_top_comments(int(story_id), max_comments_to_fetch, max_comments_to_keep)
                if fetch_top_comments and story_id
                else []
            )
            return record, article_body, comments

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(enrich_one, record) for record in records]
            for future in as_completed(futures):
                try:
                    record, body, comments = future.result()
                except Exception:
                    continue
                if body:
                    record.context_content["original_content"] = body[:8000]
                    record.content = body
                if comments:
                    record.context_content["top_comments"] = comments
                    record.context_content["top_comments_basis"] = "hackernews_api_order"
        return records

    def _story_record(self, story_id: int, seen: set[str], cutoff: datetime) -> SourceRecord | bool | None:
        try:
            story = _fetch_hn_item(story_id)
        except Exception:
            return None
        if not story or story.get("type") != "story" or story.get("dead") or story.get("deleted"):
            return None
        timestamp = story.get("time")
        if not timestamp:
            return None
        published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if published_at < cutoff:
            return False
        score = int(story.get("score") or 0)
        title = str(story.get("title") or "").strip()
        if not title:
            return None
        hn_page = f"https://news.ycombinator.com/item?id={story_id}"
        url = story.get("url") or hn_page
        if url in seen:
            return None
        seen.add(url)
        author = story.get("by", "")
        post_text = _clean_html(story.get("text", ""))
        return SourceRecord(
            identity=f"hn:{story_id}",
            url=url,
            title=title,
            content=post_text,
            metrics={"score": score, "comments": story.get("descendants", 0), "hn_id": story_id, "hn_url": hn_page},
            context_content={"hn_post_text": post_text, "hn_url": hn_page, "external_url": story.get("url") or ""},
            author_id=author,
            author_url=f"https://news.ycombinator.com/user?id={author}" if author else "",
            source_published_date=published_at,
        )

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
