"""Helpers shared by sources that read server-rendered pages instead of an API.

Next.js App Router pages ship their data as React flight chunks:
``self.__next_f.push([1, "<json string>"])``. Concatenating the decoded chunks
gives a text stream in which the page's records appear as plain JSON objects,
so a source can pick its records out by the keys they must carry.

Leaderboard-style sources (weekly or monthly boards, revenue rankings) show
the same leaders for many days in a row. ``recent_*`` keep the identities a
config emitted in its scraper state so the next run picks the next best entry
instead of re-sending one the daily pipeline has already shown.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Sequence
from datetime import date, timedelta
from typing import Any

_FLIGHT_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_DECODER = json.JSONDecoder()

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


class SourceUnavailable(RuntimeError):
    """The source could not be read. Raised instead of returning no records, so
    the run records a failed task rather than a silent zero-item success."""


def next_flight_text(html: str) -> str:
    chunks = [json.loads(match.group(1)) for match in _FLIGHT_CHUNK.finditer(html or "")]
    return "".join(chunks)


def iter_json_objects(text: str, required_keys: Sequence[str]) -> Iterator[dict[str, Any]]:
    """Yields every JSON object in `text` that carries all `required_keys`.

    Objects are found by their opening brace and decoded with raw_decode, so
    nested objects are yielded too when they match on their own.
    """
    if not required_keys:
        raise ValueError("required_keys must not be empty")
    if json.dumps(required_keys[0]) not in text:
        return
    for match in re.finditer(r"\{", text):
        try:
            value, _ = _DECODER.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict) and all(key in value for key in required_keys):
            yield value


def unique_by(values: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: dict[Any, dict[str, Any]] = {}
    for value in values:
        marker = value.get(key)
        if marker in (None, "") or marker in seen:
            continue
        seen[marker] = value
    return list(seen.values())


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def recent_identities(state: dict[str, Any], snapshot_date: str, ttl_days: int) -> set[str]:
    """Identities emitted within `ttl_days` before `snapshot_date` (same-day reruns excluded)."""
    if ttl_days <= 0:
        return set()
    today = _parse_day(snapshot_date)
    recent = state.get("recent") if isinstance(state, dict) else None
    if today is None or not isinstance(recent, dict):
        return set()
    cutoff = today - timedelta(days=ttl_days)
    out = set()
    for identity, day in recent.items():
        emitted = _parse_day(day)
        if emitted and cutoff < emitted < today:
            out.add(str(identity))
    return out


def remember_identities(state: dict[str, Any], identities: Iterable[str], snapshot_date: str, ttl_days: int) -> None:
    """Records `identities` as emitted on `snapshot_date` and drops entries older than the ttl."""
    today = _parse_day(snapshot_date)
    if today is None:
        return
    recent = state.get("recent") if isinstance(state.get("recent"), dict) else {}
    cutoff = today - timedelta(days=max(ttl_days, 1))
    kept = {key: day for key, day in recent.items() if (_parse_day(day) or today) > cutoff}
    for identity in identities:
        kept[str(identity)] = today.isoformat()
    state["recent"] = kept
