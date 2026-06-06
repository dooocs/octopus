# scrapers/github_search.py

import os
import re
import requests
from datetime import datetime, timedelta
from infra.models import BaseScraper, RawItem
from infra.oss import upload_images_to_oss, upload_image_to_oss
from scrapers.registry import register

_DEFAULT_BADGE_PATTERNS = [
    "shields.io", "badgen.net", "img.shields.io",
    "badge", "ci-badge", "codecov.io", "travis-ci",
    "github.com/workflows", "actions/workflows",
    "hits.dwyl.com", "visitor-badge", "star-history.com",
]


def _get_headers(token: str) -> dict:
    return {"Authorization": f"token {token}"}


def _is_noise_image(url: str, patterns: list[str] | None = None) -> bool:
    url_lower = url.lower()
    return any(p in url_lower for p in (patterns or _DEFAULT_BADGE_PATTERNS))


def _extract_readme_images(raw_text: str, owner: str, repo: str, max_images: int = 3, badge_patterns: list[str] | None = None) -> list[str]:
    if not raw_text:
        return []
    md_images = re.findall(r'!\[.*?\]\((.*?)\)', raw_text)
    html_images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', raw_text, re.IGNORECASE)
    all_urls = md_images + html_images
    result = []
    for url in all_urls:
        url = url.strip()
        if not url or _is_noise_image(url, badge_patterns):
            continue
        if url.startswith("./") or (not url.startswith("http") and not url.startswith("//")):
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/refs/heads/main/{url.lstrip('./')}"
        result.append(url)
        if len(result) >= max_images:
            break
    return result


def _clean_readme(text: str) -> str:
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'^\s*\[.*?\]\(.*?\)\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _fetch_readme_raw(owner: str, repo: str, token: str) -> str:
    try:
        headers = {"Accept": "application/vnd.github.raw"}
        if token:
            headers["Authorization"] = f"token {token}"
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/readme",
            headers=headers, timeout=10,
        )
        if resp.status_code != 200:
            return ""
        return resp.text
    except Exception:
        return ""


def _fetch_languages(owner: str, repo: str, token: str) -> str:
    try:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/languages",
            headers=headers, timeout=10,
        )
        if resp.status_code != 200:
            return ""
        langs = list(resp.json().keys())[:5]
        return f"主要语言：{', '.join(langs)}\n\n" if langs else ""
    except Exception:
        return ""


def _star_history_url(owner: str, repo: str) -> str:
    return f"https://api.star-history.com/svg?repos={owner}/{repo}&type=Date"


def _oss_date_str(snapshot_date: str | None = None) -> str | None:
    return snapshot_date.replace("-", "") if snapshot_date else None


@register("github_search")
class GitHubSearchEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        token = os.getenv("GH_MODELS_TOKEN")
        if not token:
            print("⚠️ GH_MODELS_TOKEN 未设置")
            return []

        headers = _get_headers(token)
        fetch_days = self.config.get("fetch_window_days", 7)
        last_week = (datetime.now() - timedelta(days=fetch_days)).strftime("%Y-%m-%d")
        per_page = self.config.get("per_page", 30)
        badge_patterns = self.config.get("badge_patterns", _DEFAULT_BADGE_PATTERNS)
        max_images = self.config.get("max_readme_images", 3)
        oss_date = _oss_date_str(self.snapshot_date)

        queries = self.config.get("queries", [
            {"q": f"created:>={last_week} stars:>100 topic:ai", "label": "AI topic"},
        ])

        seen, items = set(), []

        for query_cfg in queries:
            q_template = query_cfg["q"]
            q = q_template.replace("{last_week}", last_week)
            label = query_cfg["label"]

            try:
                res = requests.get(
                    "https://api.github.com/search/repositories",
                    headers=headers,
                    params={"q": q, "sort": "stars", "order": "desc", "per_page": per_page},
                    timeout=15,
                )
                if res.status_code == 403:
                    print(f"⚠️ GitHub API 频率限制，跳过: {label}")
                    continue
                if res.status_code != 200:
                    print(f"⚠️ GitHub Search 异常 [{res.status_code}] ({label})")
                    continue

                results = res.json().get("items", [])
                new_count = 0
                for r in results:
                    url = r["html_url"]
                    if url in seen:
                        continue
                    seen.add(url)
                    new_count += 1

                    owner = r["owner"]["login"]
                    repo = r["name"]

                    readme_raw = _fetch_readme_raw(owner, repo, token)
                    readme_images = _extract_readme_images(readme_raw, owner, repo, max_images, badge_patterns)
                    readme_images = upload_images_to_oss(readme_images, oss_date)
                    readme_clean = _clean_readme(readme_raw) if readme_raw else ""
                    star_history = _star_history_url(owner, repo)
                    star_history = upload_image_to_oss(star_history, oss_date) or star_history
                    lang_prefix = _fetch_languages(owner, repo, token)
                    body_text = lang_prefix + readme_clean if readme_clean else (r.get("description") or "")

                    items.append(RawItem(
                        title=r["full_name"],
                        original_url=url,
                        source_name=self.name,
                        source_type=self.config.get("source_type", "REPO"),
                        content_type=self.config.get("content_type", "repo"),
                        author=owner,
                        author_url=f"https://github.com/{owner}",
                        body_text=body_text,
                        raw_metrics={
                            "stars": r["stargazers_count"],
                            "forks": r["forks_count"],
                            "watchers": r.get("watchers_count", 0),
                            "open_issues": r.get("open_issues_count", 0),
                        },
                        extra={
                            "language": r.get("language"),
                            "topics": r.get("topics", []),
                            "created_at": r.get("created_at"),
                            "search_query": label,
                            "description": r.get("description") or "",
                            "readme_images": readme_images,
                            "star_history_url": star_history,
                        },
                        published_at=datetime.fromisoformat(
                            r["created_at"].replace("Z", "+00:00")
                        ) if r.get("created_at") else None,
                    ))

                print(f"  [{label}] 返回 {len(results)} 条，新增 {new_count} 条")

            except Exception as e:
                print(f"⚠️ GitHub Search 失败 ({label}): {e}")

        print(f"  抓取到 {len(items)} 条原始数据（去重后）")
        return items
