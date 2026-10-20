"""Original posts of watched X accounts, one time window per run (the 动态 tab).

Design: docs/feed-pipeline-design.md §4.

- Window: from the cursor the last successful run left in the scraper state
  (minus a small overlap) to now. Scheduled 06:00–01:00 Beijing time, so the
  06:00 run covers 01:00–06:00 and the others one hour; a late or skipped run
  never leaves a gap. The cursor only moves when the whole task succeeds
  (the runtime persists ctx.state after a successful run only).
- Query: all accounts in as few advanced searches as possible, bounded by the
  window, instead of one timeline page per account (billing is per returned
  tweet).
- Kept: original posts and the author's replies to themselves (threads).
  Dropped: retweets, quote posts, replies to other accounts. Each tweet is its
  own raw_item; threads are merged by the processing layer from
  context_content.thread.
- Text: t.co links are resolved before storing (clean_text): the link X
  appends for the tweet's own photo/video is removed (the media itself is in
  context_content.media and archived to OSS), every other t.co link becomes
  its expanded URL. displayTextRange is not used: it ends at ~280 characters
  on long posts.
- Media: photos, video covers, videos/GIFs (an mp4 of at most 720p, up to
  50MB) and avatars are archived to OSS (media/x/…). X's own URLs are never
  stored: normalize strips them, and media that failed to archive is dropped.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from crawler.core.contracts import (
    ChannelSpec,
    RawItem,
    RunContext,
    ScraperConfig,
    SourceAdapterBase,
    SourceRecord,
    enrich_enabled,
)
from crawler.core.registry import register_adapter
from infra.gateways.oss import OSS_CUSTOM_DOMAIN, archive_media, archive_video

from .spec_helpers import INTEGER, STRING_ARRAY, default_input, input_schema
from .twitterapi_io import TwitterApiClient, media_of, parse_created_at, require_api_key

SEARCH_ENDPOINT = "/twitter/tweet/advanced_search"
MEDIA_PREFIX = "media/x"
# Length budget of the "(from:a OR from:b …)" part of one query.
MAX_FROM_CLAUSE_CHARS = 400
CURSOR_KEY = "feed_cursor"

KIND_ORIGINAL = "original"
KIND_THREAD = "thread_reply"
KIND_RETWEET = "retweet"
KIND_QUOTE = "quote"
KIND_REPLY = "reply"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_cursor(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fetch_window(state: dict[str, Any], now: datetime, *, max_lookback_hours: int, overlap_minutes: int) -> tuple[datetime, datetime]:
    """[since, until) of this run."""
    earliest = now - timedelta(hours=max_lookback_hours)
    cursor = _parse_cursor(state.get(CURSOR_KEY))
    since = earliest if cursor is None else max(earliest, cursor - timedelta(minutes=overlap_minutes))
    return since, now


def account_batches(accounts: list[str], max_chars: int = MAX_FROM_CLAUSE_CHARS) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    length = 0
    for account in accounts:
        term = f"from:{account}"
        extra = len(term) + (4 if current else 0)  # " OR "
        if current and length + extra > max_chars:
            batches.append(current)
            current, length = [], 0
            extra = len(term)
        current.append(account)
        length += extra
    if current:
        batches.append(current)
    return batches


def build_query(accounts: list[str], since: datetime, until: datetime) -> str:
    clause = " OR ".join(f"from:{a}" for a in accounts)
    return f"({clause}) since_time:{int(since.timestamp())} until_time:{int(until.timestamp())}"


def classify(raw: dict[str, Any]) -> str:
    author_id = str((raw.get("author") or {}).get("id") or "")
    text = str(raw.get("text") or "")
    if raw.get("retweeted_tweet") or text.startswith("RT @"):
        return KIND_RETWEET
    if raw.get("quoted_tweet"):
        return KIND_QUOTE
    if raw.get("isReply") or raw.get("inReplyToId"):
        reply_to = str(raw.get("inReplyToUserId") or "")
        return KIND_THREAD if reply_to and reply_to == author_id else KIND_REPLY
    return KIND_ORIGINAL


_TRAILING_SPACE = re.compile(r"[ \t]+(?=\n|$)")
_EXTRA_BLANK_LINES = re.compile(r"\n{3,}")


def clean_text(raw: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Tweet text with t.co resolved, and the links it contains.

    Returns (text, links): links are [{"url": expanded, "display_url": …}] in
    order of appearance, for the site to label long URLs.
    """
    text = str(raw.get("text") or "")
    container = raw.get("extendedEntities") or raw.get("extended_entities") or {}
    for item in (container.get("media") if isinstance(container, dict) else None) or []:
        if isinstance(item, dict) and item.get("url"):
            text = text.replace(str(item["url"]), "")
    links: list[dict[str, str]] = []
    entities = raw.get("entities") or {}
    for item in (entities.get("urls") if isinstance(entities, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        short, expanded = str(item.get("url") or ""), str(item.get("expanded_url") or "")
        if not short or not expanded:
            continue
        if short in text:
            text = text.replace(short, expanded)
            links.append({"url": expanded, "display_url": str(item.get("display_url") or expanded)})
    text = _EXTRA_BLANK_LINES.sub("\n\n", _TRAILING_SPACE.sub("", text)).strip()
    return text, links


def _normalize_handle(value: str) -> str:
    return value.strip().lstrip("@").lower()


@register_adapter
class TwitterFeedAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="twitter_feed",
        label="Twitter / X 动态（博主时间线）",
        group="Social",
        default_source_type="social",
        default_item_type="post",
        input_schema_version=1,
        input_schema=input_schema(
            source={"watch_accounts": STRING_ARRAY},
            fetch={"max_lookback_hours": INTEGER, "overlap_minutes": INTEGER, "max_pages": INTEGER},
            enrich_names=["media_archive"],
        ),
        default_input=default_input(
            source={"watch_accounts": []},
            fetch={"max_lookback_hours": 24, "overlap_minutes": 5, "max_pages": 25},
            enrich=[{"name": "media_archive"}],
        ),
        required_secrets=["TWITTERAPI_IO_KEY"],
        supported_enrichers=["media_archive"],
        description="按时间窗口抓取关注账号的原创推文和自己的 thread（排除转推、引用、回复他人），图片和视频转存 OSS，不保存 X 的原始媒体链接。",
    )

    # Tests inject an httpx transport here.
    transport = None

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        accounts = [_normalize_handle(str(a)) for a in config.input.source.get("watch_accounts", []) if str(a).strip()]
        accounts = list(dict.fromkeys(a for a in accounts if a))
        if not accounts:
            raise ValueError("twitter_feed needs at least one account in input.source.watch_accounts")
        fetch = config.input.fetch
        since, until = fetch_window(
            ctx.state,
            _utcnow(),
            max_lookback_hours=int(fetch.get("max_lookback_hours") or 24),
            overlap_minutes=int(fetch.get("overlap_minutes") or 5),
        )
        max_pages = int(fetch.get("max_pages") or 25)

        tweets: dict[str, dict[str, Any]] = {}
        truncated: list[str] = []
        with TwitterApiClient(require_api_key(), transport=self.transport) as client:
            for batch in account_batches(accounts):
                query = build_query(batch, since, until)
                cursor = ""
                for page in range(max_pages):
                    body = client.get(SEARCH_ENDPOINT, {"query": query, "queryType": "Latest", "cursor": cursor})
                    for raw in body.get("tweets") or []:
                        if isinstance(raw, dict) and raw.get("id"):
                            tweets[str(raw["id"])] = raw
                    cursor = str(body.get("next_cursor") or "")
                    if not body.get("has_next_page") or not cursor:
                        break
                else:
                    truncated.append(",".join(batch))
            requests = client.requests

        if truncated:
            print(f"  ⚠️ twitter_feed: stopped after {max_pages} pages for {truncated}; raise fetch.max_pages if this repeats")
        print(
            f"  twitter_feed window {since.isoformat()} → {until.isoformat()}: "
            f"{len(tweets)} tweets in {requests} request(s)"
        )
        ctx.state[CURSOR_KEY] = until.isoformat()
        ctx.state["last_window"] = {"since": since.isoformat(), "until": until.isoformat(), "tweets": len(tweets), "requests": requests}

        watched = set(accounts)
        records: list[SourceRecord] = []
        for raw in sorted(tweets.values(), key=lambda t: str(t.get("id"))):
            record = self._to_record(raw, watched)
            if record is not None:
                records.append(record)
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        kept = [r for r in records if r.extra.get("kind") in (KIND_ORIGINAL, KIND_THREAD)]
        dropped: dict[str, int] = {}
        for record in records:
            kind = str(record.extra.get("kind"))
            if kind not in (KIND_ORIGINAL, KIND_THREAD):
                dropped[kind] = dropped.get(kind, 0) + 1
        if dropped:
            print(f"  twitter_feed dropped: {dropped}")
        return kept

    def enrich(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        if not enrich_enabled(config, "media_archive"):
            return records
        avatars: dict[str, dict[str, Any] | None] = {}
        stats = MediaStats()
        for record in records:
            record.context_content["media"] = archive_post_media(record.context_content.get("media", []), stats)
            avatar_source = record.extra.get("avatar_source_url")
            if avatar_source:
                if avatar_source not in avatars:
                    avatars[avatar_source] = archive_media(avatar_source, f"{MEDIA_PREFIX}/avatars")
                avatar = avatars[avatar_source]
                if avatar:
                    record.extra["avatar_url"] = avatar["url"]
        print(f"  twitter_feed media: {stats} avatars={sum(1 for a in avatars.values() if a)}")
        return records

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        strip_x_sources(record.context_content, record.extra)
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

    def _to_record(self, raw: dict[str, Any], watched: set[str]) -> SourceRecord | None:
        author = raw.get("author") or {}
        handle = str(author.get("userName") or "")
        if _normalize_handle(handle) not in watched:
            return None  # a search hit from an account we do not watch
        tweet_id = str(raw["id"])
        text, links = clean_text(raw)
        kind = classify(raw)
        conversation_id = str(raw.get("conversationId") or tweet_id)
        url = f"https://x.com/{handle}/status/{tweet_id}"
        return SourceRecord(
            identity=f"tweet:{tweet_id}",
            url=url,
            title=text[:100] or f"@{handle} 发布了媒体内容",
            content=text,
            metrics={
                "likes": int(raw.get("likeCount") or 0),
                "retweets": int(raw.get("retweetCount") or 0),
                "replies": int(raw.get("replyCount") or 0),
                "quotes": int(raw.get("quoteCount") or 0),
                "views": int(raw.get("viewCount") or 0),
            },
            context_content={
                "thread": {
                    "conversation_id": conversation_id,
                    "is_root": kind == KIND_ORIGINAL and conversation_id == tweet_id,
                    "in_reply_to_id": str(raw.get("inReplyToId") or "") or None,
                },
                # source_url / video_source_url are X's CDN; they only live until
                # enrich has archived the files and are stripped in normalize.
                "media": [media_source(m) for m in media_of(raw)],
                "links": links,
            },
            extra={
                "tweet_id": tweet_id,
                "kind": kind,
                "lang": raw.get("lang") or "",
                "display_name": author.get("name") or handle,
                "author_platform_id": str(author.get("id") or ""),
                "avatar_source_url": _full_size_avatar(author.get("profilePicture")),
            },
            author_id=handle,
            author_url=f"https://x.com/{handle}",
            source_published_date=parse_created_at(str(raw["createdAt"])),
        )


VIDEO_TYPES = ("video", "animated_gif")
# Keys of an archived media item kept in raw_items (and copied to the site).
ARCHIVED_KEYS = ("type", "url", "key", "width", "height", "video_url", "video_key", "video_too_large", "duration_ms")


def media_source(m: Any) -> dict[str, Any]:
    """A MediaRef as a not-yet-archived media item."""
    item: dict[str, Any] = {"type": m.type, "source_url": m.url, "width": m.width, "height": m.height}
    if m.type in VIDEO_TYPES:
        item.update({"media_key": m.key, "video_source_url": m.video_url, "duration_ms": m.duration_ms})
    return item


def video_key(item: dict[str, Any]) -> str:
    """OSS key of a video: by X's media_key, so it is known before downloading."""
    raw = str(item.get("media_key") or "")
    if not raw:
        raw = hashlib.sha256(str(item.get("video_source_url") or "").split("?")[0].encode()).hexdigest()[:32]
    return f"{MEDIA_PREFIX}/video/{re.sub(r'[^0-9A-Za-z_-]', '_', raw)}.mp4"


@dataclass
class MediaStats:
    images: int = 0
    image_failed: int = 0
    videos: int = 0
    video_too_large: int = 0
    video_failed: int = 0
    video_missing: int = 0  # no mp4 variant in the API response

    def __str__(self) -> str:
        return " ".join(f"{k}={v}" for k, v in self.__dict__.items())


def archive_post_media(items: list[dict[str, Any]], stats: MediaStats) -> list[dict[str, Any]]:
    """Archives a post's media to OSS; returns only the archived items, without X URLs.

    Photos: the image. Videos and GIFs: the cover image (required, it is the
    poster) and the mp4. A video over the size cap keeps its cover with
    video_too_large; one whose mp4 failed keeps its cover only (next run retries).
    """
    archived: list[dict[str, Any]] = []
    for item in items:
        if item.get("url") and not item.get("source_url"):  # already archived
            archived.append(item)
            continue
        cover = archive_media(str(item.get("source_url") or ""), MEDIA_PREFIX) if item.get("source_url") else None
        if cover is None:
            stats.image_failed += 1
            continue
        stats.images += 1
        out: dict[str, Any] = {"type": item.get("type"), "url": cover["url"], "key": cover["key"],
                               "width": item.get("width"), "height": item.get("height")}
        if item.get("type") in VIDEO_TYPES:
            if item.get("duration_ms"):
                out["duration_ms"] = item["duration_ms"]
            source = item.get("video_source_url")
            if not source:
                stats.video_missing += 1
            else:
                video = archive_video(str(source), video_key(item))
                if video and video.get("url"):
                    stats.videos += 1
                    out.update(video_url=video["url"], video_key=video["key"])
                elif video and video.get("too_large"):
                    stats.video_too_large += 1
                    out["video_too_large"] = True
                else:
                    stats.video_failed += 1
        archived.append(out)
    return archived


def strip_x_sources(context_content: dict[str, Any], extra: dict[str, Any]) -> bool:
    """Drops every X CDN URL from a feed row in place; returns whether anything changed.

    Media that never made it to OSS is dropped with its source URL: the site
    only ever shows our own copies.
    """
    changed = extra.pop("avatar_source_url", None) is not None
    media = context_content.get("media")
    if isinstance(media, list):
        kept = [
            {k: m[k] for k in ARCHIVED_KEYS if m.get(k) is not None}
            for m in media
            if isinstance(m, dict) and str(m.get("url") or "").startswith(f"https://{OSS_CUSTOM_DOMAIN}/")
        ]
        if kept != media:
            context_content["media"] = kept
            changed = True
    return changed


def _full_size_avatar(url: Any) -> str | None:
    if not url:
        return None
    # X serves "_normal" (48px) thumbnails by default; "_400x400" is the full avatar.
    return str(url).replace("_normal.", "_400x400.")
