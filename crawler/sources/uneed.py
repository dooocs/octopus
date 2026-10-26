from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord, enrich_enabled
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get

from .spec_helpers import INTEGER, default_input, input_schema
from .web_helpers import SourceUnavailable

UNEED_API = "https://mcp.uneed.best/v1"
UNEED_TZ = ZoneInfo("America/Los_Angeles")  # Uneed's launch days are Los Angeles days


def _clean_html(text: str) -> str:
    value = html.unescape(text or "")
    value = re.sub(r"</(p|li|h\d)>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def _get_json(path: str, params: dict | None = None) -> dict:
    response = http_get(f"{UNEED_API}{path}", params=params or {}, timeout=20, headers={"User-Agent": "octopus-crawler/0.1"})
    if response.status_code != 200:
        raise SourceUnavailable(f"Uneed API {path} returned HTTP {response.status_code}: {response.text[:200]}")
    data = response.json()
    if not isinstance(data, dict):
        raise SourceUnavailable(f"Uneed API {path} returned non-object JSON")
    return data


@register_adapter
class UneedAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="uneed",
        label="Uneed",
        group="Products",
        default_source_type="PRODUCT",
        default_item_type="product",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"day_offset": INTEGER, "limit": INTEGER},
            filters={"min_votes": INTEGER},
            enrich_names=["product_details"],
        ),
        default_input=default_input(
            fetch={"day_offset": 1, "limit": 3},
            filters={"min_votes": 0},
            enrich=[{"name": "product_details", "when": "always"}],
        ),
        supported_enrichers=["product_details"],
        description="Uneed 公开 REST API：某个洛杉矶日（默认昨天，已结束的一天）的每日投票榜 top N。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        fetch = config.input.fetch
        day_offset = int(fetch.get("day_offset") if fetch.get("day_offset") is not None else 1)
        limit = int(fetch.get("limit") or 3)
        day = (datetime.now(UNEED_TZ) - timedelta(days=day_offset)).date().isoformat()
        data = _get_json("/trending", {"period": "daily", "date": day, "limit": max(limit * 3, 10)})
        ranking = data.get("ranking")
        if not isinstance(ranking, list):
            raise SourceUnavailable("Uneed trending response has no ranking array")

        records: list[SourceRecord] = []
        for entry in ranking:
            slug = str(entry.get("slug") or "")
            name = str(entry.get("name") or "").strip()
            if not slug or not name:
                continue
            launch_date = entry.get("launch_date") or day
            records.append(
                SourceRecord(
                    identity=f"uneed:{slug}",
                    url=str(entry.get("uneed_url") or f"https://www.uneed.best/tool/{slug}"),
                    title=name,
                    content=str(entry.get("description") or ""),
                    metrics={"votes": int(entry.get("vote_count") or 0), "rank": int(entry.get("rank") or 0)},
                    extra={
                        "slug": slug,
                        "website": entry.get("url"),
                        "category": entry.get("category"),
                        "pricing": entry.get("pricing"),
                        "logo": entry.get("logo"),
                        "launch_date": launch_date,
                        "ranking_date": day,
                        "source_tag": "uneed",
                    },
                    source_published_date=datetime.fromisoformat(f"{launch_date}T00:00:00").replace(tzinfo=UNEED_TZ).astimezone(timezone.utc),
                )
            )
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        limit = int(config.input.fetch.get("limit") or 3)
        min_votes = int(config.input.filters.get("min_votes") or 0)
        kept = [r for r in records if int(r.metrics.get("votes") or 0) >= min_votes]
        kept.sort(key=lambda r: (int(r.metrics.get("rank") or 999), -int(r.metrics.get("votes") or 0)))
        return kept[:limit]

    def enrich(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        if not enrich_enabled(config, "product_details"):
            return records
        for record in records:
            try:
                detail = _get_json(f"/products/{record.extra['slug']}")
            except Exception as exc:  # the ranking entry is still usable on its own
                record.context_content["product_details_status"] = f"error:{exc.__class__.__name__}"
                continue
            rich = _clean_html(str(detail.get("rich_description") or ""))
            if rich:
                record.content = f"{record.content}\n\n{rich}".strip()
            record.extra["tags"] = detail.get("tags")
            record.extra["open_source"] = detail.get("open_source")
            record.extra["repo_url"] = detail.get("repo_url")
            record.context_content["product_details_status"] = "ok"
        return records

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        return RawItem(
            title=record.title,
            original_url=record.url,
            source_name=config.name,
            source_type=config.source_type,
            item_type=config.item_type,
            identity=record.identity,
            body_text=record.content,
            raw_metrics=record.metrics,
            extra=record.extra,
            context_content=record.context_content,
            published_at=record.source_published_date,
        )
