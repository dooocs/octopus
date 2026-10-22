"""feed_digest: the 动态 tab's processing (docs/feed-pipeline-design.md §5.2).

raw_items of the feed (one row per tweet) → posts (a tweet, or a thread
merged under its first tweet) → filter → Chinese translation per post →
proc_feed_posts. The site shows the translation, grouped by author (display
name and avatar are on every post row).

Filter (2026-10-22): the site shows only standalone posts, and only those a
general reader can enjoy without context.
- Standalone: a post is shown only when its first tweet is the author's own
  original post. A thread whose head was a quote, a retweet or a reply to
  someone else (dropped by the crawler) would show up as a fragment, so it is
  hidden without an LLM call.
- AI filter (input.filter): one short LLM call labels each post 保留 / 政治 /
  硬核 / 广告 / 缺上下文; labels in filter.hide are hidden. Posts with no words
  get no call: kept when they carry media, 缺上下文 otherwise (a lone "4"). The label is cached
  per content and filter version, like translations.
- Hidden posts are written to proc_feed_hidden (with the reason, for review)
  and removed from proc_feed_posts, so the site needs no change. A post that
  fails to classify is left out and retried, like a failed translation.

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
      "prompt_version": 1,
      "filter": {"version": 1, "hide": ["政治", "硬核", "广告", "缺上下文"]}
    }
Without "filter" no LLM filtering happens; the standalone rule always applies.
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
HIDDEN_TABLE = "proc_feed_hidden"
# url is the image (a video's cover); video_url the archived mp4, when there is one.
MEDIA_KEYS = ("type", "url", "width", "height", "video_url", "video_too_large", "duration_ms")

# Shown directly below the original, so: faithful, same paragraphs, nothing added.
TRANSLATE_SYSTEM = (
    "你是专业的科技翻译。把用户给出的 X（Twitter）帖子忠实翻译成简体中文，译文会显示在原文下方供对照阅读。"
    "要求：只输出译文，不加任何解释、前言或引号；保留 @用户名、#话题、链接、代码、数字和产品/模型/公司名的原文；"
    "语气贴近原文，口语就译成口语；段落和换行与原文一一对应。"
)

LABEL_KEEP = "保留"
LABEL_NO_CONTEXT = "缺上下文"
FILTER_LABELS = (LABEL_KEEP, "政治", "硬核", "广告", LABEL_NO_CONTEXT)
REASON_NOT_STANDALONE = "非独立帖子"

# The reader: Chinese, mostly developers and indie makers, scrolling in spare
# moments. One label, nothing else, so parsing stays trivial.
FILTER_SYSTEM = (
    "你是一个中文资讯站「动态」栏目的内容审核员。读者是国内的开发者和独立开发者，在碎片时间刷这个栏目，"
    "想看有意思、有观点、有数字、有故事的帖子。给你一条 X（Twitter）帖子，判断它属于下面哪一类，只输出类别名，不要输出任何其他文字：\n"
    "政治：涉及选举、政党、政客、政府施政或监管之争、国家间关系、战争、移民、意识形态或文化战争，或任何与中国政治相关的内容；"
    "AI 公司和政府、监管机构的博弈也算；嘲讽或攻击媒体、某个群体、某种立场的言论也算。只是顺带提到某个国家或公司，不算。\n"
    "硬核：需要专业背景才能看懂的深度技术内容，比如贴代码、讲算法或论文细节、底层系统原理、跑分表格。"
    "普通人能看懂的技术新闻、新产品发布、使用感受，不算。\n"
    "广告：替别人推广的付费内容（帖子在夸某家公司的产品，而作者不是这家公司的人）、带货、优惠码、抽奖、卖课。"
    "作者发布或介绍自己做的产品不算广告，用调侃、恶搞方式写的“发布”也不算，这两种都选「保留」。\n"
    "缺上下文：只有看到别的内容才能看懂，而这些内容不在帖子里：评论另一条帖子或图片（如“this”“转给你在德州的朋友”）、"
    "只有一个词/数字/表情的反应、以 @某人 开头的对话回复。\n"
    "保留：不属于以上任何一类。\n"
    "拿不准时，选「保留」。"
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


def has_words(text: str) -> bool:
    """Any letters left once links and @mentions are removed."""
    return any(ch.isalpha() for ch in _MENTION.sub(" ", _URL.sub(" ", text)))


def parse_label(answer: str) -> str:
    """The filter label in an LLM answer; raises when there is none."""
    cleaned = answer.strip().strip("「」“”\"'*` 。.")
    if cleaned in FILTER_LABELS:
        return cleaned
    found = [label for label in FILTER_LABELS if label in answer]
    if len(found) == 1:
        return found[0]
    raise ValueError(f"filter answer has no single label: {answer[:80]!r}")


def filter_input(post: "Post") -> str:
    text = post.text_original
    if post.media:
        text += f"\n\n（附带 {len(post.media)} 张图片或视频）"
    return text


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
    # The first tweet of the post is the author's own original post (in the data).
    standalone: bool = True

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
        root = next((p for p in parts if _tweet_id(p) == conversation), None)
        standalone = root is not None
        if root is None:
            root = parts[0]
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
            standalone=standalone,
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
    filter_config = config.input.get("filter") or None
    filter_version = int(filter_config.get("version") or 1) if filter_config else None
    hide_labels = {str(label) for label in (filter_config or {}).get("hide") or []}
    unknown = hide_labels - set(FILTER_LABELS)
    if unknown:
        raise ValueError(f"filter.hide has unknown labels {sorted(unknown)}; known: {FILTER_LABELS}")
    since = deps.now() - window

    rows = deps.raw_items.select_published_since(sub_source_types, since)
    roots = deps.raw_items.select_by_ids(missing_root_ids(rows))
    posts = build_posts([*rows, *roots])

    posts_table = deps.table(POSTS_TABLE)
    hidden_table = deps.table(HIDDEN_TABLE)
    ids = [p.id for p in posts]
    existing = _existing(posts_table, "id", ids)
    existing_hidden = _existing(hidden_table, "id", ids)
    llm = None

    def client():
        nonlocal llm
        if llm is None:
            llm = deps.llm(config.input.get("llm") or {})  # a missing key fails the whole run here
        return llm

    post_rows: list[dict[str, Any]] = []
    hidden_rows: list[dict[str, Any]] = []
    translated = reused = skipped = classified = labels_reused = 0
    hidden_counts: dict[str, int] = {}
    errors: list[str] = []
    for post in posts:
        before = existing.get(post.id)
        hid = existing_hidden.get(post.id)

        # 1. Filter: which posts the site shows at all.
        if not post.standalone:
            label = REASON_NOT_STANDALONE
        elif filter_version is None:
            label = LABEL_KEEP
        elif not has_words(post.text_original):
            # Nothing to judge: photos/videos stand on their own; a lone number,
            # emoji or link does not (a reaction to something that is not shown).
            label = LABEL_KEEP if post.media else LABEL_NO_CONTEXT
        elif hid and hid.get("content_hash") == post.content_hash and int(hid.get("filter_version") or 0) == filter_version:
            label = str(hid.get("reason") or "")
            labels_reused += 1
        elif before and before.get("content_hash") == post.content_hash and int(before.get("filter_version") or 0) == filter_version:
            label = LABEL_KEEP
            labels_reused += 1
        else:
            chat = client()  # outside the try: a missing key fails the whole run
            try:
                label = parse_label(chat.complete(FILTER_SYSTEM, filter_input(post), max_tokens=20))
            except Exception as exc:  # left out and retried next run, never shown unfiltered
                errors.append(f"{post.url}: filter: {exc.__class__.__name__}: {exc}")
                continue
            classified += 1
        if label == REASON_NOT_STANDALONE or label in hide_labels:
            hidden_counts[label] = hidden_counts.get(label, 0) + 1
            hidden_rows.append({
                "id": post.id,
                "author": post.author,
                "url": post.url,
                "published_at": post.published_at.isoformat(),
                "text_original": post.text_original,
                "reason": label,
                "content_hash": post.content_hash,
                "filter_version": filter_version or 0,
            })
            continue

        # 2. Translation of the posts that are shown.
        if before and before.get("content_hash") == post.content_hash and int(before.get("prompt_version") or 0) == prompt_version:
            text_zh = str(before.get("text_zh") or "")
            reused += 1
        elif not needs_translation(post.text_original, post.langs):
            text_zh = ""
            skipped += 1
        else:
            chat = client()
            try:
                text_zh = _clean(chat.complete(TRANSLATE_SYSTEM, post.text_original, max_tokens=2000))
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
            "filter_version": filter_version,
        })

    # Hidden first, then removed from the site's table; shown posts leave the hidden table.
    if hidden_rows:
        hidden_table.upsert(hidden_rows, on_conflict="id")
        stale = [row["id"] for row in hidden_rows if row["id"] in existing]
        for chunk in _chunks(stale):
            posts_table.delete({"id": "in.(" + ",".join(f'"{k}"' for k in chunk) + ")"})
    if post_rows:
        posts_table.upsert(post_rows, on_conflict="id")
        unhidden = [row["id"] for row in post_rows if row["id"] in existing_hidden]
        for chunk in _chunks(unhidden):
            hidden_table.delete({"id": "in.(" + ",".join(f'"{k}"' for k in chunk) + ")"})

    summary = {
        "window_start": since.isoformat(),
        "raw_items": len(rows),
        "thread_roots_fetched": len(roots),
        "posts": len(posts),
        "shown": len(post_rows),
        "hidden": hidden_counts,
        "classified": classified,
        "labels_reused": labels_reused,
        "translated": translated,
        "translations_reused": reused,
        "no_translation_needed": skipped,
        "failed": len(errors),
        "llm": llm.usage.to_dict() if llm is not None else {"calls": 0},
    }
    if errors:
        summary["errors"] = errors[:20]
        written = bool(post_rows or hidden_rows)
        raise PipelineIncomplete(f"{len(errors)} of {len(posts)} posts failed to process; the rest were saved", summary, partial=written)
    return summary
