# scrapers/reddit.py

import time
import requests
from datetime import datetime, timezone
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

REDDIT_TOP_URL = "https://www.reddit.com/r/{subreddit}/top.json"
USER_AGENT = "AmazingIndex/1.0 by /u/amazingindex"


def _retry_get(url: str, params: dict, headers: dict, max_retries: int = 3) -> requests.Response:
    """指数退避重试：1s / 3s / 9s"""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
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


@register("reddit")
class RedditEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        subreddit = self.config.get("subreddit", "LocalLLaMA")
        min_score = self.config.get("min_score", 50)
        skip_nsfw = self.config.get("skip_nsfw", True)
        skip_stickied = self.config.get("skip_stickied", True)
        skip_discussion_below = self.config.get("skip_discussion_below", 100)
        skip_self_text_below = self.config.get("skip_self_text_below", 200)
        max_retries = self.config.get("max_retries", 3)
        source_type = self.config.get("source_type", "NEWS")
        content_type = self.config.get("content_type", "reddit")

        headers = {"User-Agent": USER_AGENT}
        t0 = time.time()

        fetched = 0
        skipped = 0
        errors = 0
        items = []

        try:
            resp = _retry_get(
                REDDIT_TOP_URL.format(subreddit=subreddit),
                params={"t": "day", "limit": 25},
                headers=headers,
                max_retries=max_retries,
            )
            if resp.status_code != 200:
                print(f"  ❌ Reddit r/{subreddit} 返回 HTTP {resp.status_code}")
                errors += 1
                return []

            data = resp.json()
            posts = data.get("data", {}).get("children", [])
        except Exception as e:
            print(f"  ❌ Reddit r/{subreddit} 请求失败: {e}")
            errors += 1
            return []

        for child in posts:
            post = child.get("data", {})
            fetched += 1

            # 过滤：NSFW
            if skip_nsfw and post.get("over_18"):
                skipped += 1
                continue

            # 过滤：置顶帖
            if skip_stickied and post.get("stickied"):
                skipped += 1
                continue

            # 过滤：score 阈值
            score = post.get("score", 0)
            if score < min_score:
                skipped += 1
                continue

            # 过滤：Discussion flair 低分帖
            flair = post.get("link_flair_text", "")
            if flair == "Discussion" and score < skip_discussion_below:
                skipped += 1
                continue

            # 过滤：短自言自语
            is_self = post.get("is_self", False)
            selftext = post.get("selftext", "")
            if is_self and len(selftext) < skip_self_text_below:
                skipped += 1
                continue

            # 字段映射
            title = post.get("title", "").strip()
            if not title:
                skipped += 1
                continue

            permalink = post.get("permalink", "")
            url = f"https://reddit.com{permalink}" if permalink else ""
            if not url:
                skipped += 1
                continue

            # summary：自帖用 selftext，外链帖用 title + domain
            if is_self:
                summary = selftext[:500] if selftext else ""
            else:
                domain = post.get("domain", "")
                summary = f"{title} · {domain}" if domain else title

            # published_at
            created_utc = post.get("created_utc")
            published_at = datetime.fromtimestamp(created_utc, tz=timezone.utc) if created_utc else None

            # author
            author = post.get("author", "")

            item = RawItem(
                title=title,
                original_url=url,
                source_name=self.name,
                source_type=source_type,
                content_type=content_type,
                author=author,
                author_url=f"https://reddit.com/user/{author}" if author else "",
                body_text=summary,
                raw_metrics={"score": score, "comments": post.get("num_comments", 0)},
                extra={
                    "subreddit": subreddit,
                    "upvote_ratio": post.get("upvote_ratio"),
                    "flair": flair,
                    "post_id": post.get("id", ""),
                    "is_self": is_self,
                    "external_url": post.get("url", "") if not is_self else "",
                    "source_tag": f"reddit_{subreddit.lower()}",
                },
                published_at=published_at,
            )
            items.append(item)

        duration_ms = int((time.time() - t0) * 1000)
        print(f"  [{self.name}] fetched={fetched} new={len(items)} skipped={skipped} errors={errors} duration={duration_ms}ms")
        return items
