"""twitterapi.io access shared by the Twitter adapters.

Every failure is an exception. An empty result is only ever an empty list the
API actually returned: out of credits, a bad key or an error body must fail the
task (and the workflow), never look like "no new tweets". Between 2026-07-08
and 2026-10 the old behaviour turned an exhausted balance into months of
"success, 0 items".
"""

from __future__ import annotations

import os
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


def media_of(raw: dict[str, Any]) -> list[MediaRef]:
    """Photos and video covers attached to a tweet.

    The documented tweet schema has no media field; responses carry X's
    `extendedEntities.media` (or `extended_entities`). Read defensively and
    verify against live data.
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
        refs.append(
            MediaRef(
                type=str(item.get("type") or "photo"),
                url=str(url),
                width=_int_or_none(info.get("width")),
                height=_int_or_none(info.get("height")),
            )
        )
    return refs


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
