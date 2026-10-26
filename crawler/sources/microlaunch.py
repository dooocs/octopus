from __future__ import annotations

from datetime import datetime

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get

from .spec_helpers import INTEGER, NUMBER, default_input, input_schema
from .web_helpers import (
    BROWSER_USER_AGENT,
    SourceUnavailable,
    iter_json_objects,
    next_flight_text,
    recent_identities,
    remember_identities,
)

MICROLAUNCH_HOME = "https://microlaunch.net/"
MICROLAUNCH_PRODUCT_URL = "https://microlaunch.net/p/{slug}"


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.startswith("$") else text


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@register_adapter
class MicroLaunchAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="microlaunch",
        label="MicroLaunch",
        group="Products",
        default_source_type="PRODUCT",
        default_item_type="product",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"limit": INTEGER, "repeat_ttl_days": INTEGER},
            filters={"min_score_week": NUMBER},
        ),
        default_input=default_input(
            fetch={"limit": 1, "repeat_ttl_days": 30},
            filters={"min_score_week": 5},
        ),
        description="MicroLaunch 当月批次的周榜（首页 Next.js 数据），按 score_week 取尚未发过的 top N；同一产品 30 天内不重复。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        response = http_get(MICROLAUNCH_HOME, timeout=30, headers={"User-Agent": BROWSER_USER_AGENT})
        if response.status_code != 200:
            raise SourceUnavailable(f"MicroLaunch returned HTTP {response.status_code}")
        launches: dict[str, dict] = {}
        for obj in iter_json_objects(next_flight_text(response.text), ["slug", "votes_week", "batch_num"]):
            slug = str(obj.get("slug") or "")
            if slug and slug not in launches:
                launches[slug] = obj
        if not launches:
            raise SourceUnavailable("MicroLaunch page carried no launch records (page format changed?)")
        current_batch = max(int(obj.get("batch_num") or 0) for obj in launches.values())

        records: list[SourceRecord] = []
        for slug, launch in launches.items():
            if int(launch.get("batch_num") or 0) != current_batch or _text(launch.get("status")) != "live":
                continue
            name = _text(launch.get("codename"))
            if not name:
                continue
            problem = _text(launch.get("problem_label"))
            description = _text(launch.get("description"))
            started = _text(launch.get("start_date"))
            keywords = launch.get("all_kws") if isinstance(launch.get("all_kws"), list) else []
            records.append(
                SourceRecord(
                    identity=f"microlaunch:{slug}",
                    url=MICROLAUNCH_PRODUCT_URL.format(slug=slug),
                    title=name,
                    content="\n".join(dict.fromkeys(part for part in [problem, description] if part)),
                    metrics={
                        "votes": int(launch.get("votes") or 0),
                        "votes_week": int(launch.get("votes_week") or 0),
                        "score_week": _number(launch.get("score_week")),
                    },
                    extra={
                        "slug": slug,
                        "website": _text(launch.get("url")) or None,
                        "market": _text(launch.get("market")) or None,
                        "product_type": _text(launch.get("product_type")) or None,
                        "keywords": keywords[:8],
                        "batch_num": current_batch,
                        "source_tag": "microlaunch",
                    },
                    author_id=_text(launch.get("maker_name")),
                    source_published_date=datetime.fromisoformat(f"{started}T00:00:00+00:00") if started else None,
                )
            )
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        fetch = config.input.fetch
        limit = int(fetch.get("limit") or 1)
        ttl = int(fetch.get("repeat_ttl_days") if fetch.get("repeat_ttl_days") is not None else 30)
        min_score = _number(config.input.filters.get("min_score_week"))
        seen = recent_identities(ctx.state, ctx.snapshot_date, ttl)
        kept = [r for r in records if r.identity not in seen and _number(r.metrics.get("score_week")) >= min_score]
        kept.sort(key=lambda r: (_number(r.metrics.get("score_week")), int(r.metrics.get("votes") or 0)), reverse=True)
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
            author=record.author_id,
            body_text=record.content,
            raw_metrics=record.metrics,
            extra=record.extra,
            context_content=record.context_content,
            published_at=record.source_published_date,
        )
