from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime, timezone

import feedparser
import requests

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord, enrich_enabled
from crawler.core.registry import register_adapter
from infra.gateways.http_transport import http_get, http_post

from .spec_helpers import BOOLEAN, INTEGER, STRING, default_input, input_schema
from .web_helpers import SourceUnavailable

REDDIT_TOP_URL = "https://www.reddit.com/r/{subreddit}/top.json"
REDDIT_COMMENTS_URL = "https://www.reddit.com/comments/{post_id}.json"
REDDIT_TOP_RSS_URL = "https://www.reddit.com/r/{subreddit}/top/.rss"
REDDIT_COMMENTS_RSS_URL = "https://www.reddit.com/r/{subreddit}/comments/{post_id}/.rss"
REDDIT_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_OAUTH_TOP_URL = "https://oauth.reddit.com/r/{subreddit}/top"
REDDIT_OAUTH_COMMENTS_URL = "https://oauth.reddit.com/comments/{post_id}"
USER_AGENT = "AmazingIndex/1.0 by /u/amazingindex"


def _retry_get(url: str, params: dict, headers: dict, max_retries: int = 3) -> requests.Response:
    for attempt in range(max_retries):
        try:
            response = http_get(url, params=params, headers=headers, timeout=15)
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


def _clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _rss_html_to_text(value: str) -> str:
    """Reddit RSS bodies are HTML: a preview table cell, the post's markdown div,
    then a "submitted by /u/x [link] [comments]" footer."""
    text = html.unescape(value or "")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"</(p|li|h\d|tr)>|<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s*submitted by\s+/u/\S+.*$", "", text, flags=re.S)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
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


@register_adapter
class RedditAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="reddit",
        label="Reddit",
        group="Community",
        default_source_type="community",
        default_item_type="discussion",
        input_schema_version=1,
        input_schema=input_schema(
            source={"subreddit": STRING},
            filters={
                "min_score": INTEGER,
                "skip_nsfw": BOOLEAN,
                "skip_stickied": BOOLEAN,
                "skip_discussion_below": INTEGER,
                "skip_self_text_below": INTEGER,
            },
            fetch={"max_retries": INTEGER, "post_limit": INTEGER, "max_comments_to_keep": INTEGER, "keep_top": INTEGER},
            enrich_names=["top_comments"],
        ),
        default_input=default_input(
            source={"subreddit": "LocalLLaMA"},
            filters={
                "min_score": 50,
                "skip_nsfw": True,
                "skip_stickied": True,
                "skip_discussion_below": 100,
                "skip_self_text_below": 200,
            },
            fetch={"max_retries": 3, "post_limit": 10, "max_comments_to_keep": 10},
            enrich=[{"name": "top_comments", "when": "always"}],
        ),
        supported_enrichers=["top_comments"],
        description=(
            "抓取指定 subreddit 过去 24 小时的高分讨论（t=day）。有 REDDIT_CLIENT_ID/SECRET 时走 OAuth，"
            "否则走公开 JSON；JSON 被拒（GitHub Actions IP 常见 403）时退回 RSS。keep_top>0 时只保留最热的 N 条。"
        ),
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        source = config.input.source
        fetch = config.input.fetch
        subreddit = str(source.get("subreddit") or "LocalLLaMA")
        max_retries = int(fetch.get("max_retries") if fetch.get("max_retries") is not None else 3)
        post_limit = int(fetch.get("post_limit") if fetch.get("post_limit") is not None else 10)

        headers = {"User-Agent": USER_AGENT}
        oauth_headers = _reddit_oauth_headers()
        if oauth_headers:
            headers = oauth_headers
            top_url = REDDIT_OAUTH_TOP_URL.format(subreddit=subreddit)
            api_mode = "oauth"
        else:
            top_url = REDDIT_TOP_URL.format(subreddit=subreddit)
            api_mode = "public_json"

        try:
            resp = _retry_get(top_url, params={"t": "day", "limit": post_limit}, headers=headers, max_retries=max_retries)
            json_status = f"HTTP {resp.status_code}"
            posts = resp.json().get("data", {}).get("children", []) if resp.status_code == 200 else None
        except Exception as exc:
            json_status = f"{exc.__class__.__name__}: {exc}"
            posts = None
        if posts is None:
            return self._fetch_rss_fallback(
                subreddit, max(post_limit, 10), {"User-Agent": USER_AGENT}, "rss_fallback", json_status=f"{api_mode} {json_status}"
            )

        records: list[SourceRecord] = []
        for child in posts:
            post = child.get("data", {})
            score = int(post.get("score") or 0)
            flair = post.get("link_flair_text", "")
            is_self = bool(post.get("is_self", False))
            selftext = post.get("selftext", "")
            title = str(post.get("title") or "").strip()
            permalink = post.get("permalink", "")
            url = f"https://reddit.com{permalink}" if permalink else ""
            if not title or not url:
                continue
            summary = selftext[:4000] if is_self else f"{title} · {post.get('domain', '')}".strip(" ·")
            created_utc = post.get("created_utc")
            published_at = datetime.fromtimestamp(created_utc, tz=timezone.utc) if created_utc else None
            author = post.get("author", "")
            post_id = post.get("id", "")
            records.append(
                SourceRecord(
                    identity=f"reddit:{post_id}",
                    url=url,
                    title=title,
                    content=summary,
                    metrics={"score": score, "comments": post.get("num_comments", 0)},
                    extra={
                        "subreddit": subreddit,
                        "upvote_ratio": post.get("upvote_ratio"),
                        "flair": flair,
                        "post_id": post_id,
                        "is_self": is_self,
                        "over_18": bool(post.get("over_18")),
                        "stickied": bool(post.get("stickied")),
                        "external_url": post.get("url", "") if not is_self else "",
                        "source_tag": f"reddit_{subreddit.lower()}",
                        "discover_via": api_mode,
                    },
                    context_content={"post_text": selftext if is_self else "", "top_comments": []},
                    author_id=author,
                    author_url=f"https://reddit.com/user/{author}" if author else "",
                    source_published_date=published_at,
                )
            )
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        filters = config.input.filters
        min_score = int(filters.get("min_score") if filters.get("min_score") is not None else 50)
        skip_nsfw = bool(filters.get("skip_nsfw", True))
        skip_stickied = bool(filters.get("skip_stickied", True))
        skip_discussion_below = int(filters.get("skip_discussion_below") if filters.get("skip_discussion_below") is not None else 100)
        skip_self_text_below = int(filters.get("skip_self_text_below") if filters.get("skip_self_text_below") is not None else 200)

        pruned: list[SourceRecord] = []
        for record in records:
            score = int(record.metrics.get("score") or 0)
            # RSS carries no score (it is already ordered by top); score filters only apply to JSON records.
            has_score = record.extra.get("fallback") != "rss"
            if has_score and score < min_score:
                continue
            if skip_nsfw and record.extra.get("over_18"):
                continue
            if skip_stickied and record.extra.get("stickied"):
                continue
            flair = str(record.extra.get("flair") or "")
            if has_score and flair == "Discussion" and score < skip_discussion_below:
                continue
            post_text = str(record.context_content.get("post_text") or "")
            if record.extra.get("is_self") and len(post_text) < skip_self_text_below:
                continue
            pruned.append(record)
        keep_top = int(config.input.fetch.get("keep_top") or 0)
        if keep_top > 0:
            # Stable sort: RSS records (all score 0) keep the feed's top order.
            pruned = sorted(pruned, key=lambda r: int(r.metrics.get("score") or 0), reverse=True)[:keep_top]
        return pruned

    def enrich(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        if not enrich_enabled(config, "top_comments"):
            return records
        max_retries = int(config.input.fetch.get("max_retries") or 3)
        max_comments_to_keep = int(config.input.fetch.get("max_comments_to_keep") or 10)
        oauth_headers = _reddit_oauth_headers()
        if oauth_headers:
            headers = oauth_headers
            comments_url_template = REDDIT_OAUTH_COMMENTS_URL
            comments_fetch_via = "oauth"
        else:
            headers = {"User-Agent": USER_AGENT}
            comments_url_template = REDDIT_COMMENTS_URL
            comments_fetch_via = "public_json"
        for record in records:
            post_id = record.extra.get("post_id", "")
            top_comments, status = _fetch_top_comments(
                str(post_id),
                headers,
                max_retries,
                max_comments_to_keep,
                comments_url_template=comments_url_template,
            )
            via = comments_fetch_via
            if status != "ok":
                rss_comments, rss_status = self._fetch_rss_comments(
                    str(record.extra.get("subreddit") or ""), str(post_id), max_comments_to_keep
                )
                if rss_status == "ok":
                    top_comments, status, via = rss_comments, "ok", "rss"
                else:
                    status = f"{status}; rss {rss_status}"
            record.context_content["top_comments"] = top_comments
            record.context_content["top_comments_basis"] = "reddit_top_sort"
            record.context_content["comments_fetch_status"] = status
            record.context_content["comments_fetch_via"] = via
        return records

    def _fetch_rss_comments(self, subreddit: str, post_id: str, limit: int) -> tuple[list[dict], str]:
        if not subreddit or not post_id or limit <= 0:
            return [], "disabled"
        try:
            resp = http_get(
                REDDIT_COMMENTS_RSS_URL.format(subreddit=subreddit, post_id=post_id),
                params={"sort": "top", "limit": limit},
                headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"},
                timeout=15,
            )
            if resp.status_code != 200:
                return [], f"http_{resp.status_code}"
            feed = feedparser.parse(resp.text)
        except Exception as exc:
            return [], f"error:{exc.__class__.__name__}"
        comments = []
        for entry in feed.entries[1:]:  # the first entry is the post itself
            text = _rss_html_to_text(getattr(entry, "content", [{}])[0].get("value", "") if getattr(entry, "content", None) else getattr(entry, "summary", ""))
            if not text:
                continue
            comments.append(
                {
                    "id": str(getattr(entry, "id", "") or ""),
                    "author": str(getattr(entry, "author", "") or "").replace("/u/", ""),
                    "text": text[:1000],
                    "score": None,
                    "created_at": getattr(entry, "updated", None),
                }
            )
            if len(comments) >= limit:
                break
        return comments, "ok"

    def _fetch_rss_fallback(
        self,
        subreddit: str,
        post_limit: int,
        headers: dict,
        discover_via: str,
        *,
        json_status: str = "",
    ) -> list[SourceRecord]:
        try:
            resp = http_get(
                REDDIT_TOP_RSS_URL.format(subreddit=subreddit),
                params={"t": "day"},
                headers={**headers, "Accept": "application/atom+xml"},
                timeout=15,
            )
            rss_status = f"HTTP {resp.status_code}"
            feed = feedparser.parse(resp.text) if resp.status_code == 200 else None
        except Exception as exc:
            rss_status = f"{exc.__class__.__name__}: {exc}"
            feed = None
        if feed is None:
            raise SourceUnavailable(f"r/{subreddit}: JSON refused ({json_status}) and RSS refused ({rss_status})")
        records: list[SourceRecord] = []
        for entry in feed.entries[:post_limit]:
            title = (getattr(entry, "title", "") or "").strip()
            url = (getattr(entry, "link", "") or "").strip()
            if not title or not url:
                continue
            raw_summary = getattr(entry, "content", [{}])[0].get("value", "") if getattr(entry, "content", None) else getattr(entry, "summary", "")
            summary = _rss_html_to_text(raw_summary or "")
            score = _parse_rss_metric(r"([0-9,]+)\s+points?", summary)
            comments_count = _parse_rss_metric(r"([0-9,]+)\s+comments?", summary)
            post_id = _post_id_from_url(url)
            parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
            published_at = datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None
            author = str(getattr(entry, "author", "") or "").replace("/u/", "")
            records.append(
                SourceRecord(
                    identity=f"reddit:{post_id or url}",
                    url=url,
                    title=title,
                    content=summary[:4000],
                    metrics={"score": score, "comments": comments_count},
                    extra={
                        "subreddit": subreddit,
                        "post_id": post_id,
                        "source_tag": f"reddit_{subreddit.lower()}",
                        "fallback": "rss",
                        "discover_via": discover_via,
                    },
                    context_content={"post_text": summary, "top_comments": []},
                    author_id=author,
                    author_url=f"https://reddit.com/user/{author}" if author else "",
                    source_published_date=published_at,
                )
            )
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
