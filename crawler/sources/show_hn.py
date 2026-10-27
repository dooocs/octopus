from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get

from .spec_helpers import INTEGER, default_input, input_schema
from .web_helpers import SourceUnavailable

ALGOLIA_SEARCH_BY_DATE = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"


def _clean_html(text: str) -> str:
    value = html.unescape(text or "")
    value = re.sub(r"<p>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


@register_adapter
class ShowHnAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="show_hn",
        label="Show HN",
        group="Products",
        default_source_type="PRODUCT",
        default_item_type="product",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"window_hours": INTEGER, "hits_per_page": INTEGER, "limit": INTEGER},
            filters={"min_points": INTEGER},
        ),
        default_input=default_input(
            fetch={"window_hours": 24, "hits_per_page": 100, "limit": 5},
            filters={"min_points": 20},
        ),
        description="抓取过去 N 小时内发布的 Show HN 帖子（HN Algolia API），按 points 取 top N。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        fetch = config.input.fetch
        window_hours = int(fetch.get("window_hours") or 24)
        hits_per_page = int(fetch.get("hits_per_page") or 100)
        min_points = int(config.input.filters.get("min_points") or 0)
        cutoff = int(time.time()) - window_hours * 3600
        numeric = [f"created_at_i>{cutoff}"]
        if min_points > 0:
            numeric.append(f"points>={min_points}")

        response = http_get(
            ALGOLIA_SEARCH_BY_DATE,
            params={"tags": "show_hn", "numericFilters": ",".join(numeric), "hitsPerPage": hits_per_page},
            timeout=20,
            headers={"User-Agent": "octopus-crawler/0.1"},
        )
        if response.status_code != 200:
            raise SourceUnavailable(f"HN Algolia returned HTTP {response.status_code}")
        hits = response.json().get("hits")
        if not isinstance(hits, list):
            raise SourceUnavailable("HN Algolia response has no hits array")

        records: list[SourceRecord] = []
        for hit in hits:
            hn_id = str(hit.get("objectID") or "")
            title = str(hit.get("title") or "").strip()
            if not hn_id or not title:
                continue
            hn_url = HN_ITEM_URL.format(id=hn_id)
            created = hit.get("created_at_i")
            author = str(hit.get("author") or "")
            records.append(
                SourceRecord(
                    identity=f"hn:{hn_id}",
                    url=str(hit.get("url") or hn_url),
                    title=title,
                    content=_clean_html(str(hit.get("story_text") or "")) or title,
                    metrics={"score": int(hit.get("points") or 0), "comments": int(hit.get("num_comments") or 0)},
                    extra={"hn_id": hn_id, "hn_url": hn_url, "source_tag": "show_hn"},
                    author_id=author,
                    author_url=f"https://news.ycombinator.com/user?id={author}" if author else "",
                    source_published_date=datetime.fromtimestamp(int(created), tz=timezone.utc) if created else None,
                )
            )
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        limit = int(config.input.fetch.get("limit") or 5)
        min_points = int(config.input.filters.get("min_points") or 0)
        kept = [r for r in records if int(r.metrics.get("score") or 0) >= min_points]
        kept.sort(key=lambda r: (int(r.metrics.get("score") or 0), int(r.metrics.get("comments") or 0)), reverse=True)
        return kept[:limit]

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
