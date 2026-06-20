from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord, enrich_enabled
from core.registry import register_adapter
from infra.http import http_get
from infra.oss import upload_image_to_oss, upload_images_to_oss

from .spec_helpers import INTEGER, STRING, default_input, input_schema

_DEFAULT_BADGE_PATTERNS = [
    "shields.io",
    "badgen.net",
    "img.shields.io",
    "badge",
    "ci-badge",
    "codecov.io",
    "travis-ci",
    "github.com/workflows",
    "actions/workflows",
    "hits.dwyl.com",
    "visitor-badge",
    "star-history.com",
]

QUERY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["q", "label"],
        "properties": {"q": STRING, "label": STRING},
        "additionalProperties": False,
    },
}


def _github_token() -> str:
    return os.getenv("GH_MODELS_TOKEN") or os.getenv("GITHUB_TOKEN") or ""


def _auth_headers(token: str, *, raw: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _is_noise_image(url: str, patterns: list[str] | None = None) -> bool:
    url_lower = url.lower()
    return any(pattern in url_lower for pattern in (patterns or _DEFAULT_BADGE_PATTERNS))


def _extract_readme_images(
    raw_text: str,
    owner: str,
    repo: str,
    max_images: int = 3,
    badge_patterns: list[str] | None = None,
) -> list[str]:
    if not raw_text:
        return []
    md_images = re.findall(r"!\[.*?\]\((.*?)\)", raw_text)
    html_images = re.findall(r"<img[^>]+src=[\"']([^\"']+)[\"']", raw_text, re.IGNORECASE)
    result: list[str] = []
    for image_url in md_images + html_images:
        image_url = image_url.strip()
        if not image_url or _is_noise_image(image_url, badge_patterns):
            continue
        if image_url.startswith("./") or (not image_url.startswith("http") and not image_url.startswith("//")):
            image_url = f"https://raw.githubusercontent.com/{owner}/{repo}/refs/heads/main/{image_url.lstrip('./')}"
        result.append(image_url)
        if len(result) >= max_images:
            break
    return result


def _clean_readme(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"^\s*\[.*?\]\(.*?\)\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch_readme_raw(owner: str, repo: str, token: str) -> str:
    try:
        response = http_get(f"https://api.github.com/repos/{owner}/{repo}/readme", headers=_auth_headers(token, raw=True), timeout=10)
        if response.status_code != 200:
            return ""
        return response.text
    except Exception:
        return ""


def _fetch_languages(owner: str, repo: str, token: str) -> str:
    try:
        response = http_get(f"https://api.github.com/repos/{owner}/{repo}/languages", headers=_auth_headers(token), timeout=10)
        if response.status_code != 200:
            return ""
        languages = list(response.json().keys())[:5]
        return f"主要语言：{', '.join(languages)}\n\n" if languages else ""
    except Exception:
        return ""


def _star_history_url(owner: str, repo: str) -> str:
    return f"https://api.star-history.com/svg?repos={owner}/{repo}&type=Date"


def _oss_date_str(snapshot_date: str | None = None) -> str | None:
    return snapshot_date.replace("-", "") if snapshot_date else None


def _repo_record(
    *,
    config: ScraperConfig,
    full_name: str,
    url: str,
    owner: str,
    repo: str,
    description: str,
    metrics: dict,
    extra: dict,
    published_at: datetime | None = None,
) -> SourceRecord:
    return SourceRecord(
        identity=full_name,
        url=url,
        title=full_name,
        content=description,
        metrics=metrics,
        extra={
            "description": description,
            "owner": owner,
            "repo": repo,
            "readme_images": [],
            "star_history_url": "",
            **extra,
        },
        author_id=owner,
        author_url=f"https://github.com/{owner}" if owner else "",
        source_published_date=published_at,
    )


class _GitHubRepoEnricher(SourceAdapterBase):
    def enrich(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        token = _github_token()
        if not token:
            return records
        fetch = config.input.fetch
        badge_patterns = fetch.get("badge_patterns") or _DEFAULT_BADGE_PATTERNS
        max_images = int(fetch.get("max_readme_images") or 3)
        oss_date = _oss_date_str(ctx.snapshot_date)
        do_readme = enrich_enabled(config, "github_readme")
        do_languages = enrich_enabled(config, "github_languages")
        do_images = enrich_enabled(config, "github_images")
        do_star_history = enrich_enabled(config, "star_history")
        for record in records:
            owner = str(record.extra.get("owner") or record.author_id)
            repo = str(record.extra.get("repo") or record.title.split("/", 1)[-1])
            readme_raw = _fetch_readme_raw(owner, repo, token) if owner and do_readme else ""
            readme_clean = _clean_readme(readme_raw) if readme_raw else ""
            if do_images and readme_raw:
                readme_images = _extract_readme_images(readme_raw, owner, repo, max_images, badge_patterns)
                record.extra["readme_images"] = upload_images_to_oss(readme_images, oss_date)
            if do_star_history and owner:
                star_history = _star_history_url(owner, repo)
                record.extra["star_history_url"] = upload_image_to_oss(star_history, oss_date) or star_history
            lang_prefix = _fetch_languages(owner, repo, token) if owner and do_languages else ""
            if readme_clean or lang_prefix:
                record.content = lang_prefix + readme_clean if readme_clean else lang_prefix + record.content
            if readme_clean:
                record.context_content["readme"] = readme_clean
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


@register_adapter
class GitHubTrendingAdapter(_GitHubRepoEnricher):
    spec = ChannelSpec(
        scraper="github_trending",
        label="GitHub Trending",
        group="Code",
        default_source_type="code_host",
        default_item_type="repo",
        input_schema_version=1,
        input_schema=input_schema(
            fetch={"timeout": INTEGER},
            enrich_names=["github_readme", "github_languages", "github_images", "star_history"],
        ),
        default_input=default_input(
            fetch={"timeout": 15},
            enrich=[
                {"name": "github_readme", "when": "always"},
                {"name": "github_languages", "when": "always"},
                {"name": "github_images", "when": "has_readme"},
                {"name": "star_history", "when": "always"},
            ],
        ),
        required_secrets=["GH_MODELS_TOKEN"],
        supported_enrichers=["github_readme", "github_languages", "github_images", "star_history"],
        description="抓取 GitHub Trending 仓库榜单。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        timeout = int(config.input.fetch.get("timeout") or 15)
        res = http_get("https://github.com/trending", headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        records: list[SourceRecord] = []
        for rank, article in enumerate(soup.find_all("article", class_="Box-row"), start=1):
            title_tag = article.find("h2", class_="h3")
            if not title_tag:
                continue
            link = title_tag.find("a")
            if not link or not link.get("href"):
                continue
            full_name = "".join(title_tag.text.split())
            repo_url = f"https://github.com{link['href']}"
            desc_tag = article.find("p", class_="col-9")
            description = desc_tag.text.strip() if desc_tag else ""
            stars = 0
            meta = article.find("div", class_="f6 color-fg-muted mt-2")
            if meta:
                star_link = meta.find("a", href=lambda value: value and value.endswith("/stargazers"))
                if star_link:
                    try:
                        stars = int(star_link.text.strip().replace(",", ""))
                    except Exception:
                        pass
            parts = full_name.split("/")
            owner, repo = (parts[0], parts[1]) if len(parts) == 2 else ("", full_name)
            records.append(
                _repo_record(
                    config=config,
                    full_name=full_name,
                    url=repo_url,
                    owner=owner,
                    repo=repo,
                    description=description,
                    metrics={"stars": stars, "rank": rank},
                    extra={},
                )
            )
        return records


@register_adapter
class GitHubSearchAdapter(_GitHubRepoEnricher):
    spec = ChannelSpec(
        scraper="github_search",
        label="GitHub Search",
        group="Code",
        default_source_type="code_host",
        default_item_type="repo",
        input_schema_version=1,
        input_schema=input_schema(
            source={"queries": QUERY_SCHEMA},
            fetch={
                "per_page": INTEGER,
                "fetch_window_days": INTEGER,
                "max_readme_images": INTEGER,
                "badge_patterns": {"type": "array", "items": STRING},
            },
            filters={"min_stars": INTEGER},
            enrich_names=["github_readme", "github_languages", "github_images", "star_history"],
        ),
        default_input=default_input(
            source={"queries": [{"q": "topic:ai stars:>100", "label": "ai"}]},
            fetch={"per_page": 30, "fetch_window_days": 7},
            filters={"min_stars": 100},
            enrich=[
                {"name": "github_readme", "when": "always"},
                {"name": "github_languages", "when": "always"},
                {"name": "github_images", "when": "has_readme"},
                {"name": "star_history", "when": "always"},
            ],
        ),
        required_secrets=["GH_MODELS_TOKEN"],
        supported_enrichers=["github_readme", "github_languages", "github_images", "star_history"],
        description="按关键词搜索近期 GitHub 仓库。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        token = _github_token()
        if not token:
            return []
        fetch = config.input.fetch
        min_stars = int(config.input.filters.get("min_stars") or 0)
        fetch_days = int(fetch.get("fetch_window_days") or 7)
        last_week = (datetime.now() - timedelta(days=fetch_days)).strftime("%Y-%m-%d")
        per_page = int(fetch.get("per_page") or 30)
        queries = config.input.source.get("queries") or [{"q": f"created:>={last_week} stars:>100 topic:ai", "label": "AI topic"}]
        seen: set[str] = set()
        records: list[SourceRecord] = []
        for query_cfg in queries:
            q = str(query_cfg["q"]).replace("{last_week}", last_week)
            label = str(query_cfg.get("label") or q)
            res = http_get(
                "https://api.github.com/search/repositories",
                headers=_auth_headers(token),
                params={"q": q, "sort": "stars", "order": "desc", "per_page": per_page},
                timeout=15,
            )
            if res.status_code != 200:
                continue
            for repo in res.json().get("items", []):
                url = repo["html_url"]
                if url in seen:
                    continue
                stars = int(repo.get("stargazers_count") or 0)
                if stars < min_stars:
                    continue
                seen.add(url)
                owner = repo["owner"]["login"]
                repo_name = repo["name"]
                published_at = (
                    datetime.fromisoformat(repo["created_at"].replace("Z", "+00:00"))
                    if repo.get("created_at")
                    else None
                )
                records.append(
                    _repo_record(
                        config=config,
                        full_name=repo["full_name"],
                        url=url,
                        owner=owner,
                        repo=repo_name,
                        description=repo.get("description") or "",
                        metrics={
                            "stars": stars,
                            "forks": repo.get("forks_count", 0),
                            "watchers": repo.get("watchers_count", 0),
                            "open_issues": repo.get("open_issues_count", 0),
                        },
                        extra={
                            "language": repo.get("language"),
                            "topics": repo.get("topics", []),
                            "created_at": repo.get("created_at"),
                            "search_query": label,
                        },
                        published_at=published_at,
                    )
                )
        return records
