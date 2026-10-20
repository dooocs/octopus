"""feed_digest: the 动态 tab's processing (docs/feed-pipeline-design.md §5.2).

raw_items of the feed (one row per tweet) → posts (a tweet, or a thread
merged under its first tweet) → Chinese translation per post → proc_feed_posts.
The site shows the original text with the translation below it, grouped by
author (display name and avatar are on every post row).

- A post is translated again only when its content (its thread) or the
  prompt version changes.
- Posts already in Chinese, or with nothing to translate (only links,
  mentions or media), get no LLM call; their text_zh is "" and the site shows
  the original alone.
- A failed translation does not discard the others: the translated posts are
  written, the failed ones are left out (never shown untranslated) and retried
  by the next run, which re-reads the whole window. The run is recorded as
  partial and the workflow fails so the error is visible.

Pipeline input:
    {
      "selector": {"sub_source_types": ["x_feed_accounts"], "window_hours": 24},
      "llm": {"model": "...", "base_url": "...", "extra_body": {...}},
      "prompt_version": 1
    }
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from processing.core import Deps, PipelineConfig, PipelineIncomplete, register_pipeline

POSTS_TABLE = "proc_feed_posts"
# url is the image (a video's cover); video_url the archived mp4, when there is one.
MEDIA_KEYS = ("type", "url", "width", "height", "video_url", "video_too_large", "duration_ms")

# Shown directly below the original, so: faithful, same paragraphs, nothing added.
TRANSLATE_SYSTEM = (
    "你是专业的科技翻译。把用户给出的 X（Twitter）帖子忠实翻译成简体中文，译文会显示在原文下方供对照阅读。"
    "要求：只输出译文，不加任何解释、前言或引号；保留 @用户名、#话题、链接、代码、数字和产品/模型/公司名的原文；"
    "语气贴近原文，口语就译成口语；段落和换行与原文一一对应。"
)

_URL = re.compile(r"https?://\S+")
_MENTION = re.compile(r"@\w+")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def needs_translation(text: str, langs: list[str]) -> bool:
    """False for posts already in Chinese or with no words left once links and @mentions are removed."""
    words = _MENTION.sub(" ", _URL.sub(" ", text))
    letters = [ch for ch in words if ch.isalpha()]
    if not letters:
        return False
    if langs and all(lang.lower().startswith("zh") for lang in langs):
        return False
    # Language unknown or mixed: mostly Chinese characters means nothing to translate.
    return len(_HAN.findall(words)) * 2 < len(letters)


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Post:
    id: str
    raw_item_ids: list[str]
    author: str
    display_name: str
    avatar_url: str | None
    published_at: datetime
    updated_at: datetime
    url: str
    text_original: str
    media: list[dict[str, Any]] = field(default_factory=list)
    langs: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return _sha256(json.dumps([self.raw_item_ids, self.text_original], ensure_ascii=False))


def _tweet_id(row: dict[str, Any]) -> str:
    extra = row.get("extra") or {}
    return str(extra.get("tweet_id") or extra.get("native_id", "").removeprefix("tweet:") or row["id"])


def _conversation_id(row: dict[str, Any]) -> str:
    thread = (row.get("context_content") or {}).get("thread") or {}
    return str(thread.get("conversation_id") or _tweet_id(row))


def missing_root_ids(rows: Iterable[dict[str, Any]]) -> list[str]:
    """raw_item ids of thread roots that are not among `rows` (older than the window)."""
    present = {row["id"] for row in rows}
    wanted: set[str] = set()
    for row in rows:
        conversation = _conversation_id(row)
        if conversation != _tweet_id(row):
            root_id = _md5(f"https://x.com/{row.get('author_id')}/status/{conversation}")
            if root_id not in present:
                wanted.add(root_id)
    return sorted(wanted)


def build_posts(rows: Iterable[dict[str, Any]]) -> list[Post]:
    """Groups tweets into posts: one per (author, conversation), in thread order."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        author = str(row.get("author_id") or "")
        groups.setdefault((author.lower(), _conversation_id(row)), []).append(row)

    posts: list[Post] = []
    for (_, conversation), parts in groups.items():
        parts.sort(key=lambda r: (_as_utc(r["source_published_date"]), _tweet_id(r)))
        root = next((p for p in parts if _tweet_id(p) == conversation), parts[0])
        ordered = [root] + [p for p in parts if p is not root]
        latest = ordered[-1]
        extra = latest.get("extra") or {}
        media: list[dict[str, Any]] = []
        for part in ordered:
            for item in (part.get("context_content") or {}).get("media") or []:
                if item.get("url"):  # archived to OSS; un-archived sources are not shown
                    media.append({k: item.get(k) for k in MEDIA_KEYS if item.get(k) is not None or k in ("width", "height")})
        posts.append(Post(
            id=str(root["id"]),
            raw_item_ids=[str(p["id"]) for p in ordered],
            author=str(root.get("author_id") or ""),
            display_name=str(extra.get("display_name") or root.get("author_id") or ""),
            avatar_url=extra.get("avatar_url") or None,
            published_at=_as_utc(root["source_published_date"]),
            updated_at=max(_as_utc(p["source_published_date"]) for p in ordered),
            url=str(root["url"]),
            text_original="\n\n".join(str(p.get("content") or "").strip() for p in ordered).strip(),
            media=media,
            langs=sorted({str((p.get("extra") or {}).get("lang") or "") for p in ordered} - {""}),
        ))
    return sorted(posts, key=lambda p: p.updated_at, reverse=True)


def _clean(text: str) -> str:
    text = text.strip()
    for left, right in (("“", "”"), ('"', '"'), ("「", "」")):
        if len(text) > 1 and text.startswith(left) and text.endswith(right):
            text = text[1:-1].strip()
    return text


def _chunks(values: list[str], size: int = 100) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _existing(table, column: str, keys: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(keys):
        quoted = ",".join(f'"{k}"' for k in chunk)
        for row in table.select({column: f"in.({quoted})"}):
            found[str(row[column])] = row
    return found


@register_pipeline("feed_digest")
def run_feed_digest(config: PipelineConfig, deps: Deps) -> dict[str, Any]:
    selector = config.input.get("selector") or {}
    sub_source_types = [str(s) for s in selector.get("sub_source_types") or []]
    if not sub_source_types:
        raise ValueError("feed_digest needs input.selector.sub_source_types")
    window = timedelta(hours=int(selector.get("window_hours") or 24))
    prompt_version = int(config.input.get("prompt_version") or 1)
    since = deps.now() - window

    rows = deps.raw_items.select_published_since(sub_source_types, since)
    roots = deps.raw_items.select_by_ids(missing_root_ids(rows))
    posts = build_posts([*rows, *roots])

    posts_table = deps.table(POSTS_TABLE)
    existing = _existing(posts_table, "id", [p.id for p in posts])
    llm = None
    post_rows: list[dict[str, Any]] = []
    translated = reused = skipped = 0
    errors: list[str] = []
    for post in posts:
        before = existing.get(post.id)
        if before and before.get("content_hash") == post.content_hash and int(before.get("prompt_version") or 0) == prompt_version:
            text_zh = str(before.get("text_zh") or "")
            reused += 1
        elif not needs_translation(post.text_original, post.langs):
            text_zh = ""
            skipped += 1
        else:
            if llm is None:
                llm = deps.llm(config.input.get("llm") or {})  # a missing key fails the whole run here
            try:
                text_zh = _clean(llm.complete(TRANSLATE_SYSTEM, post.text_original, max_tokens=2000))
            except Exception as exc:  # one bad post must not hold back the others
                errors.append(f"{post.url}: {exc.__class__.__name__}: {exc}")
                continue
            translated += 1
        post_rows.append({
            "id": post.id,
            "raw_item_ids": post.raw_item_ids,
            "author": post.author,
            "display_name": post.display_name,
            "avatar_url": post.avatar_url,
            "published_at": post.published_at.isoformat(),
            "updated_at": post.updated_at.isoformat(),
            "url": post.url,
            "text_original": post.text_original,
            "text_zh": text_zh,
            "media": post.media,
            "content_hash": post.content_hash,
            "prompt_version": prompt_version,
        })
    if post_rows:
        posts_table.upsert(post_rows, on_conflict="id")

    summary = {
        "window_start": since.isoformat(),
        "raw_items": len(rows),
        "thread_roots_fetched": len(roots),
        "posts": len(posts),
        "translated": translated,
        "translations_reused": reused,
        "no_translation_needed": skipped,
        "failed": len(errors),
        "llm": llm.usage.to_dict() if llm is not None else {"calls": 0},
    }
    if errors:
        summary["errors"] = errors[:20]
        raise PipelineIncomplete(f"{len(errors)} of {len(posts)} posts failed to translate; the rest were saved", summary, partial=bool(post_rows))
    return summary
