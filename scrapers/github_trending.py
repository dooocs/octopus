# scrapers/github_trending.py

import os
import requests
from bs4 import BeautifulSoup
from infra.models import BaseScraper, RawItem
from scrapers.registry import register
from scrapers.github_search import (
    _fetch_readme_raw, _clean_readme, _extract_readme_images,
    _fetch_languages, _oss_date_str, _star_history_url,
)
from infra.oss import upload_images_to_oss, upload_image_to_oss


@register("github_trending")
class GitHubTrendingEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        token = os.getenv("GH_MODELS_TOKEN", "")
        try:
            res = requests.get(
                "https://github.com/trending",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=self.config.get("timeout", 15),
            )
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")

            items = []
            oss_date = _oss_date_str(self.snapshot_date)
            for rank, article in enumerate(soup.find_all("article", class_="Box-row"), start=1):
                title_tag = article.find("h2", class_="h3")
                if not title_tag:
                    continue
                full_name = "".join(title_tag.text.split())
                repo_url = f"https://github.com{title_tag.find('a')['href']}"

                desc_tag = article.find("p", class_="col-9")
                description = desc_tag.text.strip() if desc_tag else ""

                stars = 0
                meta = article.find("div", class_="f6 color-fg-muted mt-2")
                if meta:
                    s_link = meta.find("a", href=lambda x: x and x.endswith("/stargazers"))
                    if s_link:
                        try:
                            stars = int(s_link.text.strip().replace(",", ""))
                        except Exception:
                            pass

                parts = full_name.split("/")
                owner, repo = (parts[0], parts[1]) if len(parts) == 2 else ("", full_name)

                readme_raw = _fetch_readme_raw(owner, repo, token) if owner else ""
                readme_images = _extract_readme_images(readme_raw, owner, repo) if readme_raw else []
                readme_images = upload_images_to_oss(readme_images, oss_date)
                readme_clean = _clean_readme(readme_raw) if readme_raw else ""
                star_history = _star_history_url(owner, repo) if owner else ""
                if star_history:
                    star_history = upload_image_to_oss(star_history, oss_date) or star_history
                lang_prefix = _fetch_languages(owner, repo, token) if owner else ""
                body_text = lang_prefix + readme_clean if readme_clean else description

                items.append(RawItem(
                    title=full_name,
                    original_url=repo_url,
                    source_name=self.name,
                    source_type=self.config.get("source_type", "REPO"),
                    content_type=self.config.get("content_type", "repo"),
                    author=owner,
                    body_text=body_text,
                    raw_metrics={"stars": stars, "rank": rank},
                    extra={
                        "description": description,
                        "readme_images": readme_images,
                        "star_history_url": star_history,
                    },
                ))

            print(f"  抓取到 {len(items)} 条")
            return items
        except Exception as e:
            print(f"⚠️ GitHub Trending 失败: {e}")
            return []
