from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

import requests

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get
from infra.gateways.oss import upload_image_to_oss

from .spec_helpers import INTEGER, STRING_ARRAY, default_input, input_schema

HF_PAPERS_URL = "https://huggingface.co/api/daily_papers"
HF_MODELS_URL = "https://huggingface.co/api/models"
USER_AGENT = "AmazingIndex/1.0 (+https://amazingindex.com)"

DEFAULT_QUANT_SUFFIXES = ["-gguf", "-awq", "-gptq", "-fp8", "-int4", "-int8", "-q4_", "-q5_", "-q8_", "-bnb-"]
DEFAULT_DERIV_SUFFIXES = ["-merge", "-dpo-", "-lora-"]


def _oss_date_str(snapshot_date: str | None = None) -> str | None:
    return snapshot_date.replace("-", "") if snapshot_date else None


def _retry_get(url: str, params: dict, max_retries: int = 3, timeout: int = 15) -> requests.Response:
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(max_retries):
        try:
            response = http_get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(3**attempt)
                continue
            return response
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(3**attempt)
                continue
            raise
    return response


@register_adapter
class HuggingFaceModelsAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="hf_model",
        label="Hugging Face Models",
        group="Model",
        default_source_type="model_hub",
        default_item_type="model",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"limit": INTEGER, "max_retries": INTEGER},
            filters={
                "min_likes": INTEGER,
                "min_downloads": INTEGER,
                "quant_suffixes": STRING_ARRAY,
                "deriv_suffixes": STRING_ARRAY,
            },
        ),
        default_input=default_input(
            fetch={"limit": 3, "max_retries": 3},
            filters={"min_likes": 50, "min_downloads": 1000},
        ),
        description="抓取 Hugging Face trending models。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        fetch = config.input.fetch
        max_retries = int(fetch.get("max_retries") or 3)
        limit = int(fetch.get("limit") or 3)

        resp = _retry_get(HF_MODELS_URL, params={"sort": "trendingScore", "limit": limit}, max_retries=max_retries, timeout=20)
        if resp.status_code != 200:
            return []
        models = resp.json()
        if not isinstance(models, list):
            return []

        records: list[SourceRecord] = []
        for model in models:
            model_id = model.get("id", model.get("modelId", ""))
            if not model_id or not model.get("pipeline_tag", ""):
                continue
            likes = int(model.get("likes") or 0)
            downloads = int(model.get("downloads") or 0)
            card_data = model.get("cardData", {}) or {}
            base_model = card_data.get("base_model", "")
            description = card_data.get("description", "") or model.get("description", "")
            published_at = None
            if model.get("createdAt"):
                try:
                    published_at = datetime.fromisoformat(model["createdAt"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            records.append(
                SourceRecord(
                    identity=f"hf_model:{model_id}",
                    url=f"https://huggingface.co/{model_id}",
                    title=model_id,
                    content=description[:500] if description else "",
                    metrics={"likes": likes, "downloads": downloads},
                    extra={
                        "model_id": model_id,
                        "pipeline_tag": model.get("pipeline_tag", ""),
                        "library_name": model.get("library_name", ""),
                        "tags": model.get("tags", []),
                        "base_model": base_model,
                        "last_modified": model.get("lastModified", ""),
                        "source_tag": "ai_model",
                    },
                    author_id=model.get("author", ""),
                    source_published_date=published_at,
                )
            )
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        filters = config.input.filters
        min_likes = int(filters.get("min_likes") or 50)
        min_downloads = int(filters.get("min_downloads") or 1000)
        quant_suffixes = filters.get("quant_suffixes") or DEFAULT_QUANT_SUFFIXES
        deriv_suffixes = filters.get("deriv_suffixes") or DEFAULT_DERIV_SUFFIXES

        pruned: list[SourceRecord] = []
        for record in records:
            if int(record.metrics.get("likes") or 0) < min_likes:
                continue
            if int(record.metrics.get("downloads") or 0) < min_downloads:
                continue
            model_id_lower = str(record.extra.get("model_id") or record.title).lower()
            if any(suffix in model_id_lower for suffix in quant_suffixes):
                continue
            if any(suffix in model_id_lower for suffix in deriv_suffixes):
                continue
            if record.extra.get("base_model"):
                continue
            pruned.append(record)
        return pruned

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        return _raw_item(config, record)


@register_adapter
class HuggingFacePapersAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="hf_papers",
        label="Hugging Face Papers",
        group="Research",
        default_source_type="research",
        default_item_type="paper",
        input_schema_version=1,
        input_schema=input_schema(fetch={"top_n": INTEGER, "max_retries": INTEGER}),
        default_input=default_input(fetch={"top_n": 3, "max_retries": 3}),
        description="抓取 Hugging Face Daily Papers。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        fetch = config.input.fetch
        max_retries = int(fetch.get("max_retries") or 3)
        top_n = int(fetch.get("top_n") or 3)
        oss_date = _oss_date_str(ctx.snapshot_date)
        now_utc = datetime.now(timezone.utc)
        dates_to_try = [now_utc.strftime("%Y-%m-%d"), (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")]

        papers = []
        for date_str in dates_to_try:
            resp = _retry_get(HF_PAPERS_URL, params={"date": date_str}, max_retries=max_retries)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if isinstance(data, list) and data:
                papers = data
                break

        def upvotes(entry: dict) -> int:
            paper = entry.get("paper", entry)
            value = paper.get("upvotes", 0)
            return int(value.get("total", 0) if isinstance(value, dict) else (value or 0))

        records: list[SourceRecord] = []
        for entry in sorted(papers, key=upvotes, reverse=True)[:top_n]:
            paper = entry.get("paper", entry)
            paper_id = paper.get("id", "")
            title = str(paper.get("title", "")).strip()
            if not paper_id or not title:
                continue
            summary = paper.get("summary", paper.get("abstract", ""))
            published_at = None
            if paper.get("publishedAt"):
                try:
                    published_at = datetime.fromisoformat(paper["publishedAt"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            authors = paper.get("authors", [])
            author_names = [author.get("name", "") for author in authors if author.get("name")]
            author = ", ".join(author_names[:3])
            if len(author_names) > 3:
                author += f" et al. ({len(author_names)})"
            raw_thumbnail = entry.get("thumbnail", "")
            thumbnail_url = ""
            if raw_thumbnail and any(raw_thumbnail.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
                thumbnail_url = upload_image_to_oss(raw_thumbnail, oss_date) or raw_thumbnail
            records.append(
                SourceRecord(
                    identity=f"hf_paper:{paper_id}",
                    url=f"https://huggingface.co/papers/{paper_id}",
                    title=title,
                    content=summary[:500] if summary else "",
                    metrics={"upvotes": upvotes(entry), "num_comments": entry.get("numComments", 0)},
                    extra={
                        "paper_id": paper_id,
                        "arxiv_id": paper.get("arxivId", ""),
                        "authors": author_names,
                        "related_models": paper.get("relatedModels", []),
                        "related_datasets": paper.get("relatedDatasets", []),
                        "source_tag": "ai_research",
                        "thumbnail_url": thumbnail_url,
                        "media_urls": entry.get("mediaUrls", []),
                    },
                    author_id=author,
                    source_published_date=published_at,
                )
            )
        return records

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        return _raw_item(config, record)


def _raw_item(config: ScraperConfig, record: SourceRecord) -> RawItem:
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
