from __future__ import annotations

import threading
import time

import requests

JINA_READER_URL = "https://r.jina.ai/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_MIN_INTERVAL = 0.06
_last_request_time = 0.0
_lock = threading.Lock()

_MAX_RETRIES = 3
_BACKOFF_BASE = 2


class JinaReaderError(RuntimeError):
    pass


def _throttle() -> None:
    global _last_request_time
    with _lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_request_time = time.monotonic()


def _parse_retry_after(resp: requests.Response) -> float | None:
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("retryAfter")
    if value is None:
        return None
    try:
        return max(float(value), 1.0)
    except (TypeError, ValueError):
        return None


def fetch_jina_text(url: str, timeout: int | float = 30, max_retries: int = _MAX_RETRIES) -> str:
    """Fetch markdown/plain text for a URL through Jina Reader."""
    if not url:
        raise ValueError("url is required")

    jina_url = f"{JINA_READER_URL}{url}"
    attempts = max(max_retries, 0)

    for attempt in range(attempts + 1):
        _throttle()
        resp = requests.get(jina_url, timeout=timeout, headers=HEADERS)

        if resp.status_code == 200:
            text = resp.text.strip()
            if not text:
                raise JinaReaderError("Jina returned empty content")
            return text

        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp) or (_BACKOFF_BASE * (2**attempt))
            if attempt < attempts:
                time.sleep(retry_after)
                continue
            raise JinaReaderError(f"Jina returned 429 after {attempts} retries: {resp.text[:200]}")

        raise JinaReaderError(f"Jina returned {resp.status_code}: {resp.text[:200]}")

    raise JinaReaderError(f"Jina failed after {attempts} retries for {url}")
