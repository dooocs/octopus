"""twitterapi.io access shared by the Twitter adapters.

Every failure is an exception. An empty result is only ever an empty list the
API actually returned: out of credits, a bad key or an error body must fail the
task (and the workflow), never look like "no new tweets". Between 2026-07-08
and 2026-10 the old behaviour turned an exhausted balance into months of
"success, 0 items".
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

BASE_URL = "https://api.twitterapi.io"
API_KEY_ENV = "TWITTERAPI_IO_KEY"


class TwitterApiError(RuntimeError):
    """A request that must not be retried: auth, credits, bad request, error body."""


class TwitterApiRetryableError(RuntimeError):
    """Rate limits and server errors; retried a few times before giving up."""


def require_api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise TwitterApiError(f"{API_KEY_ENV} is not set")
    return key


def check_response(status_code: int, body: Any, text: str = "") -> dict[str, Any]:
    """Returns the JSON body of a successful response, raises otherwise."""
    snippet = (text or str(body))[:300]
    if status_code in (401, 403):
        raise TwitterApiError(f"twitterapi.io rejected the API key (HTTP {status_code}): {snippet}")
    if status_code == 402:
        raise TwitterApiError(f"twitterapi.io account is out of credits (HTTP 402): {snippet}")
    if status_code == 429 or status_code >= 500:
        raise TwitterApiRetryableError(f"twitterapi.io HTTP {status_code}: {snippet}")
    if status_code != 200:
        raise TwitterApiError(f"twitterapi.io HTTP {status_code}: {snippet}")
    if not isinstance(body, dict):
        raise TwitterApiError(f"twitterapi.io returned a non-object body: {snippet}")
    status = str(body.get("status") or "").lower()
    if status and status not in ("success", "ok"):
        message = body.get("msg") or body.get("message") or snippet
        lowered = str(message).lower()
        if "credit" in lowered or "balance" in lowered or "recharge" in lowered:
            raise TwitterApiError(f"twitterapi.io account is out of credits: {message}")
        raise TwitterApiError(f"twitterapi.io error: {message}")
    return body


class TwitterApiClient:
    """Synchronous client with bounded retries for retryable errors."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        max_attempts: int = 4,
        sleep=time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"X-API-Key": api_key},
            timeout=timeout,
            transport=transport,
        )
        self._max_attempts = max_attempts
        self._sleep = sleep
        self.requests = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TwitterApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                self.requests += 1
                resp = self._client.get(endpoint, params=params)
                try:
                    body: Any = resp.json()
                except ValueError:
                    body = None
                return check_response(resp.status_code, body, resp.text)
            except (TwitterApiRetryableError, httpx.TransportError) as exc:
                last = exc
                if attempt < self._max_attempts - 1:
                    self._sleep(min(30, 2 ** (attempt + 1)))
        raise TwitterApiError(f"twitterapi.io {endpoint} failed after {self._max_attempts} attempts: {last}")


TWEETS_BY_IDS_ENDPOINT = "/twitter/tweets"
MAX_IDS_PER_LOOKUP = 50


def tweets_by_ids(client: TwitterApiClient, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Tweets by id, 50 per request. Deleted or protected tweets are simply absent."""
    found: dict[str, dict[str, Any]] = {}
    unique = list(dict.fromkeys(str(i) for i in ids if i))
    for start in range(0, len(unique), MAX_IDS_PER_LOOKUP):
        body = client.get(TWEETS_BY_IDS_ENDPOINT, {"tweet_ids": ",".join(unique[start:start + MAX_IDS_PER_LOOKUP])})
        for raw in body.get("tweets") or []:
            if isinstance(raw, dict) and raw.get("id"):
                found[str(raw["id"])] = raw
    return found


def parse_created_at(value: str) -> datetime:
    """twitterapi.io returns RFC 2822 dates ("Thu Oct 16 06:00:00 +0000 2026") or ISO 8601."""
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class MediaRef:
    type: str  # photo | video | animated_gif
    url: str  # image URL: the photo itself, or a video's cover
    width: int | None = None
    height: int | None = None
    key: str | None = None  # X media_key (or id_str): stable per media item
    video_url: str | None = None  # videos and GIFs: the mp4 to archive (see best_mp4)
    duration_ms: int | None = None


# A video's short side must fit within this ("720p"); portrait videos count too.
MAX_VIDEO_SHORT_SIDE = 720
_RESOLUTION = re.compile(r"/(\d{2,5})x(\d{2,5})/")


def best_mp4(video_info: Any, max_short_side: int = MAX_VIDEO_SHORT_SIDE) -> str | None:
    """The mp4 to keep: the highest bitrate whose short side is at most max_short_side.

    X lists several mp4 variants (resolution in the URL path, e.g.
    /vid/avc1/1280x720/…) plus an HLS playlist, which is skipped. A variant
    whose resolution is not in the URL counts as fitting (GIFs have a single
    variant without it). When no variant fits, the smallest bitrate is taken.
    """
    variants = video_info.get("variants") if isinstance(video_info, dict) else None
    mp4s = [
        v for v in variants or []
        if isinstance(v, dict)
        and "mp4" in str(v.get("content_type") or v.get("contentType") or "").lower()
        and str(v.get("url") or "").startswith("http")
    ]
    if not mp4s:
        return None

    def short_side(v: dict[str, Any]) -> int:
        match = _RESOLUTION.search(str(v["url"]))
        return min(int(match[1]), int(match[2])) if match else 0

    def bitrate(v: dict[str, Any]) -> int:
        return _int_or_none(v.get("bitrate")) or 0

    fitting = [v for v in mp4s if short_side(v) <= max_short_side]
    chosen = max(fitting, key=bitrate) if fitting else min(mp4s, key=bitrate)
    return str(chosen["url"])


def media_of(raw: dict[str, Any]) -> list[MediaRef]:
    """Photos, and videos/GIFs (cover image plus mp4) attached to a tweet.

    The documented tweet schema has no media field; responses carry X's
    `extendedEntities.media` (or `extended_entities`), with `video_info` on
    videos and GIFs. Read defensively and verify against live data.
    """
    container = raw.get("extendedEntities") or raw.get("extended_entities") or {}
    items = container.get("media") if isinstance(container, dict) else None
    if not items and isinstance(raw.get("media"), list):
        items = raw["media"]
    refs: list[MediaRef] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = item.get("media_url_https") or item.get("media_url") or item.get("url")
        if not url or not str(url).startswith("http"):
            continue
        info = item.get("original_info") or {}
        kind = str(item.get("type") or "photo")
        video_info = item.get("video_info") or item.get("videoInfo")
        key = item.get("media_key") or item.get("id_str") or item.get("id")
        refs.append(
            MediaRef(
                type=kind,
                url=str(url),
                width=_int_or_none(info.get("width")),
                height=_int_or_none(info.get("height")),
                key=str(key) if key else None,
                video_url=best_mp4(video_info) if kind in ("video", "animated_gif") else None,
                duration_ms=_int_or_none((video_info or {}).get("duration_millis")) if isinstance(video_info, dict) else None,
            )
        )
    return refs


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
