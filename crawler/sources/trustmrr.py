from __future__ import annotations

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get

from .spec_helpers import BOOLEAN, INTEGER, NUMBER, default_input, input_schema
from .web_helpers import (
    BROWSER_USER_AGENT,
    SourceUnavailable,
    iter_json_objects,
    next_flight_text,
    recent_identities,
    remember_identities,
)

TRUSTMRR_HOME = "https://trustmrr.com/"
TRUSTMRR_STARTUP_URL = "https://trustmrr.com/startup/{slug}"


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: float) -> str:
    return f"${value:,.0f}"


@register_adapter
class TrustMrrAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="trustmrr",
        label="TrustMRR",
        group="Indie",
        default_source_type="STARTUP",
        default_item_type="startup",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"limit": INTEGER, "repeat_ttl_days": INTEGER},
            filters={"min_mrr": NUMBER, "min_prior_mrr": NUMBER, "skip_stealth": BOOLEAN},
        ),
        default_input=default_input(
            fetch={"limit": 1, "repeat_ttl_days": 30},
            filters={"min_mrr": 5000, "min_prior_mrr": 1000, "skip_stealth": True},
        ),
        description=(
            "TrustMRR 首页（Stripe 验证收入）里近 30 天 MRR 净增最多、且 30 天内没发过的 startup。"
            "按净增金额而不是百分比排序，min_prior_mrr 排除刚接入 Stripe 的（从 $2 到 $7k 不是增长）。"
        ),
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        response = http_get(TRUSTMRR_HOME, timeout=30, headers={"User-Agent": BROWSER_USER_AGENT})
        if response.status_code != 200:
            raise SourceUnavailable(f"TrustMRR returned HTTP {response.status_code}")
        startups: dict[str, dict] = {}
        for obj in iter_json_objects(next_flight_text(response.text), ["_id", "currentMrr", "slug"]):
            slug = str(obj.get("slug") or "")
            if slug and slug not in startups:
                startups[slug] = obj
        if not startups:
            raise SourceUnavailable("TrustMRR page carried no startup records (page format changed?)")

        records: list[SourceRecord] = []
        for slug, s in startups.items():
            name = str(s.get("name") or "").strip()
            mrr = _number(s.get("currentMrr"))
            growth = _number(s.get("cachedGrowthMRR30d"))
            if not name or mrr is None:
                continue
            founder = s.get("founder") if isinstance(s.get("founder"), dict) else {}
            founder_name = str(founder.get("name") or s.get("xFounderName") or "").strip()
            handle = str(founder.get("handle") or s.get("xHandle") or "").strip()
            prior = mrr / (1 + growth / 100) if growth is not None and growth > -100 else None
            added = mrr - prior if prior is not None else None
            total = _number(s.get("currentTotalRevenue"))
            last30 = _number(s.get("currentLast30DaysRevenue"))
            facts = [
                f"Stripe 验证 MRR {_money(mrr)}"
                + (f"，30 天前约 {_money(prior)}，净增 {_money(added)}（{growth:+.0f}%）" if added is not None else ""),
                f"近 30 天收入 {_money(last30)}" if last30 is not None else "",
                f"累计收入 {_money(total)}" if total is not None else "",
                f"创始人 {founder_name}" + (f"（X @{handle}）" if handle else "") if founder_name else "",
                "正在 TrustMRR 上挂牌出售" if s.get("onSale") else "",
            ]
            description = str(s.get("description") or "").strip()
            records.append(
                SourceRecord(
                    identity=f"trustmrr:{slug}",
                    url=TRUSTMRR_STARTUP_URL.format(slug=slug),
                    title=f"{name}: {_money(mrr)} MRR" + (f", +{_money(added)} ({growth:+.0f}%) in 30 days" if added is not None and added >= 0 else ""),
                    content="\n".join(part for part in [description, *facts] if part),
                    metrics={
                        "mrr": round(mrr, 2),
                        "mrr_growth_30d_pct": round(growth, 2) if growth is not None else None,
                        "mrr_prior_30d": round(prior, 2) if prior is not None else None,
                        "mrr_added_30d": round(added, 2) if added is not None else None,
                        "revenue_30d": round(last30, 2) if last30 is not None else None,
                        "total_revenue": round(total, 2) if total is not None else None,
                        "mrr_display": _money(mrr),
                        "growth_display": f"+{_money(added)} / 30 天" if added is not None and added >= 0 else None,
                    },
                    extra={
                        "slug": slug,
                        "startup_name": name,
                        "website": s.get("website"),
                        "on_sale": bool(s.get("onSale")),
                        "stealth": bool(s.get("stealthMode")),
                        "source_tag": "trustmrr",
                    },
                    author_id=founder_name,
                    author_url=f"https://x.com/{handle}" if handle else "",
                )
            )
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        fetch, filters = config.input.fetch, config.input.filters
        limit = int(fetch.get("limit") or 1)
        ttl = int(fetch.get("repeat_ttl_days") if fetch.get("repeat_ttl_days") is not None else 30)
        min_mrr = _number(filters.get("min_mrr")) or 0.0
        min_prior = _number(filters.get("min_prior_mrr")) or 0.0
        skip_stealth = bool(filters.get("skip_stealth", True))
        seen = recent_identities(ctx.state, ctx.snapshot_date, ttl)
        kept = [
            r for r in records
            if r.identity not in seen
            and (r.metrics.get("mrr") or 0) >= min_mrr
            and r.metrics.get("mrr_added_30d") is not None
            and r.metrics["mrr_added_30d"] > 0
            and (r.metrics.get("mrr_prior_30d") or 0) >= min_prior
            and not (skip_stealth and r.extra.get("stealth"))
        ]
        kept.sort(key=lambda r: r.metrics["mrr_added_30d"], reverse=True)
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
            author_url=record.author_url,
            body_text=record.content,
            raw_metrics=record.metrics,
            extra=record.extra,
            context_content=record.context_content,
        )
