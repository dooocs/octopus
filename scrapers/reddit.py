# scrapers/reddit.py

import html
import os
import re
import time
import requests
import feedparser
from datetime import datetime, timezone
from infra.http import http_get, http_post
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

REDDIT_TOP_URL = "https://www.reddit.com/r/{subreddit}/top.json"
REDDIT_COMMENTS_URL = "https://www.reddit.com/comments/{post_id}.json"
REDDIT_TOP_RSS_URL = "https://www.reddit.com/r/{subreddit}/top/.rss"
REDDIT_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_OAUTH_TOP_URL = "https://oauth.reddit.com/r/{subreddit}/top"
REDDIT_OAUTH_COMMENTS_URL = "https://oauth.reddit.com/comments/{post_id}"
USER_AGENT = "AmazingIndex/1.0 by /u/amazingindex"


def _retry_get(url: str, params: dict, headers: dict, max_retries: int = 3) -> requests.Response:
    """指数退避重试：1s / 3s / 9s"""
    for attempt in range(max_retries):
        try:
            resp = http_get(url, params=params, headers=headers, timeout=15)
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


def _clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_comment_nodes(children: list[dict], limit: int) -> list[dict]:
    comments: list[dict] = []
    for child in children:
        if child.get("kind") != "t1":
            continue
        data = child.get("data") or {}
        body = _clean_text(data.get("body", ""))
        if not body or data.get("removed") or data.get("deleted"):
            continue
        comments.append(
            {
                "id": data.get("id"),
                "author": data.get("author", ""),
                "text": body[:1000],
                "score": data.get("score", 0),
                "created_at": datetime.fromtimestamp(data.get("created_utc"), tz=timezone.utc).isoformat()
                if data.get("created_utc")
                else None,
            }
        )
        if len(comments) >= limit:
            break
    return comments


def _reddit_oauth_headers() -> dict[str, str] | None:
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    try:
        response = http_post(
            REDDIT_OAUTH_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        if response.status_code != 200:
            return None
        token = response.json().get("access_token")
        if not token:
            return None
        return {"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}
    except Exception:
        return None


def _fetch_top_comments(
    post_id: str,
    headers: dict,
    max_retries: int,
    limit: int,
    *,
    comments_url_template: str = REDDIT_COMMENTS_URL,
) -> tuple[list[dict], str]:
    if not post_id or limit <= 0:
        return [], "disabled"
    try:
        response = _retry_get(
            comments_url_template.format(post_id=post_id),
            params={"sort": "top", "limit": limit, "raw_json": 1},
            headers=headers,
            max_retries=max_retries,
        )
        if response.status_code != 200:
            return [], f"http_{response.status_code}"
        payload = response.json()
        if not isinstance(payload, list) or len(payload) < 2:
            return [], "empty"
        children = payload[1].get("data", {}).get("children", [])
        return _extract_comment_nodes(children, limit), "ok"
    except Exception as exc:
        return [], f"error:{exc.__class__.__name__}"


def _parse_rss_metric(pattern: str, text: str) -> int:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return 0
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return 0


def _post_id_from_url(url: str) -> str:
    match = re.search(r"/comments/([^/]+)/", url)
    return match.group(1) if match else ""


def _enrich_enabled(config: dict, name: str, default: bool = True) -> bool:
    enrich = config.get("enrich")
    if not isinstance(enrich, list):
        return default
    return any(isinstance(item, dict) and item.get("name") == name for item in enrich)


@register("reddit")
class RedditEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        return self.enrich_items(self.discover_items())

    def discover_items(self) -> list[RawItem]:
        subreddit = self.config.get("subreddit", "LocalLLaMA")
        min_score = self.config.get("min_score", 50)
        skip_nsfw = self.config.get("skip_nsfw", True)
        skip_stickied = self.config.get("skip_stickied", True)
        skip_discussion_below = self.config.get("skip_discussion_below", 100)
        skip_self_text_below = self.config.get("skip_self_text_below", 200)
        max_retries = self.config.get("max_retries", 3)
        post_limit = self.config.get("post_limit", 10)
        source_type = self.config.get("source_type", "NEWS")
        content_type = self.config.get("content_type", "reddit")

        headers = {"User-Agent": USER_AGENT}
        oauth_headers = _reddit_oauth_headers()
        if oauth_headers:
            headers = oauth_headers
            top_url = REDDIT_OAUTH_TOP_URL.format(subreddit=subreddit)
            api_mode = "oauth"
        else:
            top_url = REDDIT_TOP_URL.format(subreddit=subreddit)
            api_mode = "public_json"
        t0 = time.time()

        fetched = 0
        skipped = 0
        errors = 0
        items = []

        try:
            resp = _retry_get(
                top_url,
                params={"t": "day", "limit": post_limit},
                headers=headers,
                max_retries=max_retries,
            )
            if resp.status_code != 200:
                print(f"  ❌ Reddit r/{subreddit} 返回 HTTP {resp.status_code}")
                public_headers = {"User-Agent": USER_AGENT}
                return self._fetch_rss_fallback(
                    subreddit,
                    min_score,
                    max_retries,
                    post_limit,
                    source_type,
                    content_type,
                    public_headers,
                    discover_via="rss_fallback",
                )

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
                summary = selftext[:4000] if selftext else ""
            else:
                domain = post.get("domain", "")
                summary = f"{title} · {domain}" if domain else title

            # published_at
            created_utc = post.get("created_utc")
            published_at = datetime.fromtimestamp(created_utc, tz=timezone.utc) if created_utc else None

            # author
            author = post.get("author", "")
            post_id = post.get("id", "")

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
                    "post_id": post_id,
                    "is_self": is_self,
                    "external_url": post.get("url", "") if not is_self else "",
                    "source_tag": f"reddit_{subreddit.lower()}",
                    "discover_via": api_mode,
                },
                context_content={
                    "post_text": selftext if is_self else "",
                    "top_comments": [],
                },
                published_at=published_at,
            )
            items.append(item)

        duration_ms = int((time.time() - t0) * 1000)
        print(f"  [{self.name}] fetched={fetched} new={len(items)} skipped={skipped} errors={errors} duration={duration_ms}ms")
        return items

    def enrich_items(self, items: list[RawItem]) -> list[RawItem]:
        if not _enrich_enabled(self.config, "top_comments"):
            return items
        max_retries = self.config.get("max_retries", 3)
        max_comments_to_keep = self.config.get("max_comments_to_keep", 10)
        oauth_headers = _reddit_oauth_headers()
        if oauth_headers:
            headers = oauth_headers
            comments_url_template = REDDIT_OAUTH_COMMENTS_URL
            comments_fetch_via = "oauth"
        else:
            headers = {"User-Agent": USER_AGENT}
            comments_url_template = REDDIT_COMMENTS_URL
            comments_fetch_via = "public_json"
        for item in items:
            post_id = item.extra.get("post_id", "")
            top_comments, comments_status = _fetch_top_comments(
                str(post_id),
                headers,
                max_retries,
                max_comments_to_keep,
                comments_url_template=comments_url_template,
            )
            item.context_content["top_comments"] = top_comments
            item.context_content["top_comments_basis"] = "reddit_top_sort"
            item.context_content["comments_fetch_status"] = comments_status
            item.context_content["comments_fetch_via"] = comments_fetch_via
        return items

    def _fetch_rss_fallback(
        self,
        subreddit: str,
        min_score: int,
        max_retries: int,
        post_limit: int,
        source_type: str,
        content_type: str,
        headers: dict,
        discover_via: str,
    ) -> list[RawItem]:
        try:
            resp = http_get(
                REDDIT_TOP_RSS_URL.format(subreddit=subreddit),
                params={"t": "day"},
                headers={**headers, "Accept": "application/atom+xml"},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"  ❌ Reddit RSS r/{subreddit} 返回 HTTP {resp.status_code}")
                return []
            feed = feedparser.parse(resp.text)
        except Exception as exc:
            print(f"  ❌ Reddit RSS r/{subreddit} 请求失败: {exc}")
            return []

        items: list[RawItem] = []
        for entry in feed.entries[:post_limit]:
            title = (getattr(entry, "title", "") or "").strip()
            url = (getattr(entry, "link", "") or "").strip()
            if not title or not url:
                continue
            summary = _clean_text(getattr(entry, "summary", "") or "")
            score = _parse_rss_metric(r"([0-9,]+)\s+points?", summary)
            comments_count = _parse_rss_metric(r"([0-9,]+)\s+comments?", summary)
            if score and score < min_score:
                continue
            post_id = _post_id_from_url(url)
            published_at = None
            parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
            if parsed:
                published_at = datetime(*parsed[:6], tzinfo=timezone.utc)
            author = str(getattr(entry, "author", "") or "").replace("/u/", "")
            items.append(
                RawItem(
                    title=title,
                    original_url=url,
                    source_name=self.name,
                    source_type=source_type,
                    content_type=content_type,
                    author=author,
                    author_url=f"https://reddit.com/user/{author}" if author else "",
                    body_text=summary[:4000],
                    raw_metrics={"score": score, "comments": comments_count},
                    extra={
                        "subreddit": subreddit,
                        "post_id": post_id,
                        "source_tag": f"reddit_{subreddit.lower()}",
                        "fallback": "rss",
                        "discover_via": discover_via,
                    },
                    context_content={
                        "post_text": summary,
                        "top_comments": [],
                    },
                    published_at=published_at,
                )
            )
        print(f"  [{self.name}] RSS fallback new={len(items)}")
        return items
