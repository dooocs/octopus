# scrapers/huggingface.py
# HuggingFace 数据源：Daily Papers + Trending Models

import time
import requests
from datetime import datetime, timezone, timedelta
from infra.models import BaseScraper, RawItem
from infra.oss import upload_image_to_oss
from scrapers.registry import register

HF_PAPERS_URL = "https://huggingface.co/api/daily_papers"
HF_MODELS_URL = "https://huggingface.co/api/models"
USER_AGENT = "AmazingIndex/1.0 (+https://amazingindex.com)"

# 默认的量化 fork 后缀
DEFAULT_QUANT_SUFFIXES = ["-gguf", "-awq", "-gptq", "-fp8", "-int4", "-int8", "-q4_", "-q5_", "-q8_", "-bnb-"]
# 默认的衍生模型后缀
DEFAULT_DERIV_SUFFIXES = ["-merge", "-dpo-", "-lora-"]


def _oss_date_str(snapshot_date: str | None = None) -> str | None:
    return snapshot_date.replace("-", "") if snapshot_date else None


def _retry_get(url: str, params: dict, max_retries: int = 3, timeout: int = 15) -> requests.Response:
    """指数退避重试：1s / 3s / 9s"""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < max_retries - 1:
                    wait = 3 ** attempt
                    print(f"  ⚠️ HTTP {resp.status_code}，{wait}s 后重试 ({attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue
            return resp
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait = 3 ** attempt
                print(f"  ⚠️ 超时，{wait}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            raise
    return resp


# ── Daily Papers ──────────────────────────────────────────────

@register("hf_papers")
class HuggingFacePapersEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        source_type = self.config.get("source_type", "ARTICLE")
        content_type = self.config.get("content_type", "hf_papers")
        max_retries = self.config.get("max_retries", 3)
        top_n = self.config.get("top_n", 3)
        oss_date = _oss_date_str(self.snapshot_date)
        t0 = time.time()

        # 计算日期：先抓今天，如果今天没出再抓昨天
        now_utc = datetime.now(timezone.utc)
        dates_to_try = [now_utc.strftime("%Y-%m-%d"), (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")]

        papers = []
        for date_str in dates_to_try:
            try:
                resp = _retry_get(HF_PAPERS_URL, params={"date": date_str}, max_retries=max_retries)
                if resp.status_code != 200:
                    print(f"  ⚠️ HF Daily Papers 返回 HTTP {resp.status_code}（date={date_str}），降级跳过")
                    continue

                data = resp.json()
                if not isinstance(data, list):
                    print(f"  ⚠️ HF Daily Papers 返回结构异常（date={date_str}，type={type(data).__name__}），降级跳过")
                    continue

                papers = data
                if papers:
                    break
            except Exception as e:
                print(f"  ⚠️ HF Daily Papers 请求失败（date={date_str}）: {e}，降级跳过")
                continue

        fetched = 0
        skipped = 0
        errors = 0
        items = []

        # 按 upvotes 降序，取 top N
        def _upvotes(entry):
            p = entry.get("paper", entry)
            uv = p.get("upvotes", 0)
            return uv.get("total", 0) if isinstance(uv, dict) else (uv or 0)

        papers = sorted(papers, key=_upvotes, reverse=True)[:top_n]

        for entry in papers:
            fetched += 1
            try:
                paper = entry.get("paper", entry)
                paper_id = paper.get("id", "")
                if not paper_id:
                    skipped += 1
                    continue

                title = paper.get("title", "").strip()
                if not title:
                    skipped += 1
                    continue

                summary = paper.get("summary", paper.get("abstract", ""))
                url = f"https://huggingface.co/papers/{paper_id}"

                # published_at
                published_at_str = paper.get("publishedAt", "")
                published_at = None
                if published_at_str:
                    try:
                        published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                # upvotes
                upvotes = entry.get("paper", {}).get("upvotes", 0)
                if isinstance(upvotes, dict):
                    upvotes = upvotes.get("total", 0) or 0

                # authors
                authors = paper.get("authors", [])
                author_names = [a.get("name", "") for a in authors if a.get("name")]
                author = ", ".join(author_names[:3])
                if len(author_names) > 3:
                    author += f" et al. ({len(author_names)})"

                # arxiv_id
                arxiv_id = paper.get("arxivId", "")

                # related models / datasets
                related_models = paper.get("relatedModels", [])
                related_datasets = paper.get("relatedDatasets", [])

                # thumbnail (上传到 OSS，仅处理图片 URL)
                raw_thumbnail = entry.get("thumbnail", "")
                thumbnail_url = ""
                if raw_thumbnail and any(raw_thumbnail.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    thumbnail_url = upload_image_to_oss(raw_thumbnail, oss_date) or raw_thumbnail
                media_urls = entry.get("mediaUrls", [])

                item = RawItem(
                    title=title,
                    original_url=url,
                    source_name=self.name,
                    source_type=source_type,
                    content_type=content_type,
                    author=author,
                    body_text=summary[:500] if summary else "",
                    raw_metrics={"upvotes": upvotes, "num_comments": entry.get("numComments", 0)},
                    extra={
                        "paper_id": paper_id,
                        "arxiv_id": arxiv_id,
                        "authors": author_names,
                        "related_models": related_models,
                        "related_datasets": related_datasets,
                        "source_tag": "ai_research",
                        "thumbnail_url": thumbnail_url,
                        "media_urls": media_urls,
                    },
                    published_at=published_at,
                )
                items.append(item)
            except Exception as e:
                errors += 1
                print(f"  ⚠️ 解析 paper 失败: {e}")

        duration_ms = int((time.time() - t0) * 1000)
        print(f"  [{self.name}] fetched={fetched} new={len(items)} skipped={skipped} errors={errors} duration={duration_ms}ms")
        return items


# ── Trending Models ───────────────────────────────────────────

@register("hf_model")
class HuggingFaceModelsEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        source_type = self.config.get("source_type", "REPO")
        content_type = self.config.get("content_type", "hf_model")
        max_retries = self.config.get("max_retries", 3)
        min_likes = self.config.get("min_likes", 50)
        min_downloads = self.config.get("min_downloads", 1000)
        quant_suffixes = self.config.get("quant_suffixes", DEFAULT_QUANT_SUFFIXES)
        deriv_suffixes = self.config.get("deriv_suffixes", DEFAULT_DERIV_SUFFIXES)
        limit = self.config.get("limit", 3)
        t0 = time.time()

        fetched = 0
        skipped = 0
        errors = 0
        items = []

        try:
            resp = _retry_get(
                HF_MODELS_URL,
                params={"sort": "trendingScore", "limit": limit},
                max_retries=max_retries,
                timeout=20,
            )
            if resp.status_code != 200:
                print(f"  ❌ HF Models 返回 HTTP {resp.status_code}")
                return []

            models = resp.json()
            if not isinstance(models, list):
                print(f"  ❌ HF Models 返回结构异常（type={type(models).__name__}）")
                return []
        except Exception as e:
            print(f"  ❌ HF Models 请求失败: {e}")
            return []

        for model in models:
            fetched += 1
            try:
                model_id = model.get("id", model.get("modelId", ""))
                if not model_id:
                    skipped += 1
                    continue

                # 过滤：pipeline_tag 为空
                pipeline_tag = model.get("pipeline_tag", "")
                if not pipeline_tag:
                    skipped += 1
                    continue

                # 过滤：likes / downloads 阈值
                likes = model.get("likes", 0)
                downloads = model.get("downloads", 0)
                if likes < min_likes or downloads < min_downloads:
                    skipped += 1
                    continue

                # 过滤：量化 fork
                model_id_lower = model_id.lower()
                if any(suffix in model_id_lower for suffix in quant_suffixes):
                    skipped += 1
                    continue

                # 过滤：衍生模型后缀
                if any(suffix in model_id_lower for suffix in deriv_suffixes):
                    skipped += 1
                    continue

                # 过滤：base_model 不为空（衍生模型）
                card_data = model.get("cardData", {}) or {}
                base_model = card_data.get("base_model", "")
                if base_model:
                    skipped += 1
                    continue

                # 字段映射
                description = card_data.get("description", "")
                if not description:
                    # 尝试从 siblings 或其他字段获取
                    description = model.get("description", "")
                summary = description[:500] if description else ""

                created_at = model.get("createdAt", "")
                published_at = None
                if created_at:
                    try:
                        published_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                tags = model.get("tags", [])
                library_name = model.get("library_name", "")
                last_modified = model.get("lastModified", "")

                item = RawItem(
                    title=model_id,
                    original_url=f"https://huggingface.co/{model_id}",
                    source_name=self.name,
                    source_type=source_type,
                    content_type=content_type,
                    author=model.get("author", ""),
                    body_text=summary,
                    raw_metrics={"likes": likes, "downloads": downloads},
                    extra={
                        "model_id": model_id,
                        "pipeline_tag": pipeline_tag,
                        "library_name": library_name,
                        "tags": tags,
                        "base_model": base_model,
                        "last_modified": last_modified,
                        "source_tag": "ai_model",
                    },
                    published_at=published_at,
                )
                items.append(item)
            except Exception as e:
                errors += 1
                print(f"  ⚠️ 解析 model 失败: {e}")

        duration_ms = int((time.time() - t0) * 1000)
        print(f"  [{self.name}] fetched={fetched} new={len(items)} skipped={skipped} errors={errors} duration={duration_ms}ms")
        return items
