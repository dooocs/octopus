from __future__ import annotations

from datetime import datetime, timezone

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get

from .spec_helpers import INTEGER, default_input, input_schema
from .web_helpers import (
    BROWSER_USER_AGENT,
    SourceUnavailable,
    iter_json_objects,
    next_flight_text,
    recent_identities,
    remember_identities,
)

DEVHUNT_HOME = "https://devhunt.org/"
DEVHUNT_TOOL_URL = "https://devhunt.org/tool/{slug}"


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _text(value: object) -> str:
    """Flight payloads reference shared values as "$<hex>" strings; those are not content."""
    text = str(value or "").strip()
    return "" if text.startswith("$") else text


@register_adapter
class DevHuntAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="devhunt",
        label="DevHunt",
        group="Products",
        default_source_type="PRODUCT",
        default_item_type="product",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"limit": INTEGER, "repeat_ttl_days": INTEGER},
            filters={"min_votes": INTEGER},
        ),
        default_input=default_input(
            fetch={"limit": 1, "repeat_ttl_days": 7},
            filters={"min_votes": 5},
        ),
        description="DevHunt 本周 launch 榜（首页 Next.js 数据），按票数取尚未发过的 top N；同一工具 7 天内不重复。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        response = http_get(DEVHUNT_HOME, timeout=30, headers={"User-Agent": BROWSER_USER_AGENT})
        if response.status_code != 200:
            raise SourceUnavailable(f"DevHunt returned HTTP {response.status_code}")
        tools: dict[str, dict] = {}
        for obj in iter_json_objects(next_flight_text(response.text), ["slug", "votes_count", "launch_start"]):
            slug = str(obj.get("slug") or "")
            if slug and (slug not in tools or len(obj) > len(tools[slug])):
                tools[slug] = obj
        if not tools:
            raise SourceUnavailable("DevHunt page carried no launch records (page format changed?)")

        now = datetime.now(timezone.utc)
        records: list[SourceRecord] = []
        for slug, tool in tools.items():
            start, end = _parse_dt(tool.get("launch_start")), _parse_dt(tool.get("launch_end"))
            if not start or not end or not (start <= now <= end):
                continue  # past winners and upcoming tools share the page
            name = _text(tool.get("name"))
            if not name:
                continue
            categories = [c.get("name") for c in tool.get("product_categories") or [] if isinstance(c, dict) and c.get("name")]
            pricing = (tool.get("product_pricing_types") or {}).get("title") if isinstance(tool.get("product_pricing_types"), dict) else None
            slogan = _text(tool.get("slogan"))
            description = _text(tool.get("description"))
            content = "\n".join(dict.fromkeys(part for part in [slogan, description] if part))
            records.append(
                SourceRecord(
                    identity=f"devhunt:{slug}",
                    url=DEVHUNT_TOOL_URL.format(slug=slug),
                    title=name,
                    content=content,
                    metrics={"votes": int(tool.get("votes_count") or 0), "views": int(tool.get("views_count") or 0)},
                    extra={
                        "slug": slug,
                        "website": _text(tool.get("demo_url")) or None,
                        "categories": categories,
                        "pricing": pricing,
                        "launch_week_start": start.date().isoformat(),
                        "source_tag": "devhunt",
                    },
                    source_published_date=start,
                )
            )
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        fetch = config.input.fetch
        limit = int(fetch.get("limit") or 1)
        ttl = int(fetch.get("repeat_ttl_days") if fetch.get("repeat_ttl_days") is not None else 7)
        min_votes = int(config.input.filters.get("min_votes") or 0)
        seen = recent_identities(ctx.state, ctx.snapshot_date, ttl)
        kept = [r for r in records if r.identity not in seen and int(r.metrics.get("votes") or 0) >= min_votes]
        kept.sort(key=lambda r: int(r.metrics.get("votes") or 0), reverse=True)
        chosen = kept[:limit]
        remember_identities(ctx.state, [r.identity for r in chosen], ctx.snapshot_date, ttl)
        return chosen

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
