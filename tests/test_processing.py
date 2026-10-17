from __future__ import annotations

import hashlib
import json
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from infra.gateways.llm import ChatClient, LlmError
from processing.core import Deps, run_pipeline
from processing.core import PipelineIncomplete
from processing.feed_digest import build_posts, missing_root_ids, needs_translation

NOW = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)


def _id(handle: str, tweet: str) -> str:
    return hashlib.md5(f"https://x.com/{handle}/status/{tweet}".encode()).hexdigest()


def _row(handle: str, tweet: str, minutes_ago: int, text: str, conversation: str | None = None, media=None, display="Name", lang="en") -> dict:
    return {
        "id": _id(handle, tweet),
        "url": f"https://x.com/{handle}/status/{tweet}",
        "sub_source_type": "x_feed_accounts",
        "author_id": handle,
        "source_published_date": NOW - timedelta(minutes=minutes_ago),
        "content": text,
        "context_content": {"thread": {"conversation_id": conversation or tweet}, "media": media or []},
        "extra": {"tweet_id": tweet, "lang": lang, "display_name": display, "avatar_url": f"https://www.amazingindex.com/media/x/avatars/{handle}.jpg"},
    }


class FakeTable:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.upserts = 0

    def select(self, params: dict[str, str]) -> list[dict]:
        out = list(self.rows)
        for key, cond in params.items():
            if key in ("select", "limit", "order"):
                continue
            if cond.startswith("eq."):
                out = [r for r in out if str(r.get(key)) == cond[3:]]
            elif cond.startswith("in.("):
                wanted = {v.strip('"') for v in cond[4:-1].split(",")}
                out = [r for r in out if str(r.get(key)) in wanted]
        if "limit" in params:
            out = out[: int(params["limit"])]
        return out

    def upsert(self, rows: list[dict], on_conflict: str) -> None:
        self.upserts += 1
        for row in rows:
            existing = next((r for r in self.rows if r[on_conflict] == row[on_conflict]), None)
            if existing:
                existing.update(row)
            else:
                self.rows.append(dict(row))

    def insert(self, payload: dict) -> dict:
        row = {"id": f"run-{len(self.rows) + 1}", **payload}
        self.rows.append(row)
        return row

    def update(self, filters: dict[str, str], payload: dict) -> None:
        for row in self.select(filters):
            row.update(payload)


class FakeRawItems:
    def __init__(self, rows: list[dict], older: list[dict] | None = None) -> None:
        self.rows = rows
        self.older = older or []

    def select_published_since(self, subs, since):
        return [r for r in self.rows if r["sub_source_type"] in subs and r["source_published_date"] >= since]

    def select_by_ids(self, ids):
        return [r for r in self.older + self.rows if r["id"] in set(ids)]


class FakeLlm:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

        class _Usage:
            def to_dict(inner) -> dict:
                return {"calls": len(self.calls)}

        self.usage = _Usage()

    def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        self.calls.append((system, user))
        return f"“译：{user}”"


def _deps(raw: FakeRawItems, tables: dict[str, FakeTable], llm: FakeLlm, enabled: bool = True) -> Deps:
    tables.setdefault("octp_pipelines", FakeTable()).rows = [{
        "id": "p1", "slug": "feed_digest", "name": "动态", "kind": "feed_digest", "enabled": enabled,
        "input": {"selector": {"sub_source_types": ["x_feed_accounts"], "window_hours": 24}, "llm": {"model": "m"}, "prompt_version": 1},
    }]
    for name in ("octp_pipeline_runs", "proc_feed_posts"):
        tables.setdefault(name, FakeTable())
    return Deps(raw_items=raw, table=lambda name: tables[name], llm=lambda cfg: llm, now=lambda: NOW)


class BuildPostsTest(unittest.TestCase):
    def test_threads_merge_under_their_first_tweet_in_order(self) -> None:
        rows = [
            _row("karpathy", "102", 10, "3/ end", conversation="100"),
            _row("karpathy", "100", 30, "1/ start", media=[{"type": "photo", "url": "https://www.amazingindex.com/media/x/a.jpg", "source_url": "s"}]),
            _row("karpathy", "101", 20, "2/ middle", conversation="100", media=[{"type": "photo", "source_url": "not-archived"}]),
            _row("sama", "200", 5, "shipping"),
        ]
        posts = build_posts(rows)
        self.assertEqual([p.id for p in posts], [_id("sama", "200"), _id("karpathy", "100")])
        thread = posts[1]
        self.assertEqual(thread.text_original, "1/ start\n\n2/ middle\n\n3/ end")
        self.assertEqual(thread.raw_item_ids, [_id("karpathy", "100"), _id("karpathy", "101"), _id("karpathy", "102")])
        self.assertEqual(thread.published_at, NOW - timedelta(minutes=30))
        self.assertEqual(thread.updated_at, NOW - timedelta(minutes=10))
        self.assertEqual(thread.media, [{"type": "photo", "url": "https://www.amazingindex.com/media/x/a.jpg", "width": None, "height": None}])

    def test_roots_older_than_the_window_are_requested(self) -> None:
        rows = [_row("karpathy", "101", 20, "2/", conversation="100"), _row("sama", "200", 5, "x")]
        self.assertEqual(missing_root_ids(rows), [_id("karpathy", "100")])
        self.assertEqual(missing_root_ids(rows + [_row("karpathy", "100", 2000, "1/")]), [])


class NeedsTranslationTest(unittest.TestCase):
    def test_chinese_and_wordless_posts_are_skipped(self) -> None:
        self.assertTrue(needs_translation("Shipping GPT-6 today https://t.co/x", ["en"]))
        self.assertTrue(needs_translation("Le Chat est là", ["fr"]))
        self.assertFalse(needs_translation("今天发布新模型 https://t.co/x", ["zh"]))
        self.assertFalse(needs_translation("今天发布 GPT-6 新模型，欢迎试用", []), "unknown lang, mostly Han")
        self.assertFalse(needs_translation("https://t.co/abc", ["zxx"]))
        self.assertFalse(needs_translation("@sama @gdb 🔥🔥 https://t.co/abc", ["qam"]))
        self.assertTrue(needs_translation("1/ start", ["en", "zh"]), "mixed thread is translated")


class FeedDigestTest(unittest.TestCase):
    def test_translates_once_and_reuses_until_the_content_changes(self) -> None:
        old_root = _row("karpathy", "100", 60 * 30, "1/ start")  # 30h ago: outside the window
        rows = [
            _row("karpathy", "101", 20, "2/ middle", conversation="100"),
            _row("sama", "200", 5, "shipping", display="Sam Altman"),
            _row("ylecun", "300", 60 * 25, "too old"),
        ]
        raw = FakeRawItems(rows, older=[old_root])
        tables: dict[str, FakeTable] = {}
        llm = FakeLlm()

        first = run_pipeline("feed_digest", _deps(raw, tables, llm))
        self.assertEqual(first["status"], "success")
        self.assertEqual((first["posts"], first["translated"], first["thread_roots_fetched"]), (2, 2, 1))
        self.assertTrue(all("一句话" not in system for system, _ in llm.calls), "translation only, no author summary")
        posts = {r["author"]: r for r in tables["proc_feed_posts"].rows}
        self.assertEqual(posts["karpathy"]["text_original"], "1/ start\n\n2/ middle")
        self.assertEqual(posts["karpathy"]["text_zh"], "译：1/ start\n\n2/ middle", "quotes around the answer are stripped")
        self.assertEqual(posts["karpathy"]["id"], _id("karpathy", "100"))
        self.assertEqual(posts["sama"]["display_name"], "Sam Altman")
        self.assertEqual(posts["sama"]["avatar_url"], "https://www.amazingindex.com/media/x/avatars/sama.jpg")
        self.assertNotIn("ylecun", posts)
        run = tables["octp_pipeline_runs"].rows[0]
        self.assertEqual((run["status"], run["summary"]["translated"]), ("success", 2))

        calls = len(llm.calls)
        second = run_pipeline("feed_digest", _deps(raw, tables, llm))
        self.assertEqual(len(llm.calls), calls, "nothing changed: no LLM calls")
        self.assertEqual(second["translations_reused"], 2)

        rows.append(_row("karpathy", "102", 1, "3/ end", conversation="100"))
        third = run_pipeline("feed_digest", _deps(raw, tables, llm))
        self.assertEqual((third["translated"], third["translations_reused"]), (1, 1))
        posts = {r["author"]: r for r in tables["proc_feed_posts"].rows}
        self.assertTrue(posts["karpathy"]["text_zh"].endswith("3/ end"))

    def test_chinese_and_link_only_posts_skip_the_llm(self) -> None:
        rows = [
            _row("kaifulee", "400", 5, "今天聊聊大模型的推理成本", lang="zh"),
            _row("sama", "200", 6, "https://t.co/abc", lang="zxx"),
        ]
        tables: dict[str, FakeTable] = {}

        def no_llm(cfg):
            raise AssertionError("no LLM client should be created")

        deps = _deps(FakeRawItems(rows), tables, FakeLlm())
        deps.llm = no_llm
        result = run_pipeline("feed_digest", deps)
        self.assertEqual((result["posts"], result["no_translation_needed"], result["translated"]), (2, 2, 0))
        self.assertEqual({r["text_zh"] for r in tables["proc_feed_posts"].rows}, {""})

    def test_an_empty_window_needs_no_llm(self) -> None:
        tables: dict[str, FakeTable] = {}

        def no_llm(cfg):
            raise AssertionError("no LLM client should be created")

        deps = _deps(FakeRawItems([]), tables, FakeLlm())
        deps.llm = no_llm
        result = run_pipeline("feed_digest", deps)
        self.assertEqual(result["posts"], 0)

    def test_disabled_pipelines_do_nothing_unless_a_test_run_asks(self) -> None:
        tables: dict[str, FakeTable] = {}
        self.assertEqual(run_pipeline("feed_digest", _deps(FakeRawItems([]), tables, FakeLlm(), enabled=False)), {"status": "skipped"})
        self.assertEqual(tables["octp_pipeline_runs"].rows, [])
        result = run_pipeline("feed_digest", _deps(FakeRawItems([]), tables, FakeLlm(), enabled=False), include_disabled=True)
        self.assertEqual(result["status"], "success")

    def test_one_failed_translation_keeps_the_others_and_marks_the_run_partial(self) -> None:
        class Flaky(FakeLlm):
            def complete(self, system, user, *, max_tokens=2000):
                if "boom" in user:
                    raise LlmError("LLM HTTP 400: content filtered")
                return super().complete(system, user, max_tokens=max_tokens)

        rows = [_row("sama", "200", 5, "shipping"), _row("gdb", "500", 6, "boom")]
        tables: dict[str, FakeTable] = {}
        with self.assertRaisesRegex(PipelineIncomplete, "1 of 2 posts failed"):
            run_pipeline("feed_digest", _deps(FakeRawItems(rows), tables, Flaky()))
        self.assertEqual([r["author"] for r in tables["proc_feed_posts"].rows], ["sama"], "the failed post is not shown untranslated")
        run = tables["octp_pipeline_runs"].rows[0]
        self.assertEqual((run["status"], run["summary"]["failed"], run["summary"]["translated"]), ("partial", 1, 1))
        self.assertIn("content filtered", run["summary"]["errors"][0])

    def test_failures_are_recorded_and_raised(self) -> None:
        class Broken(FakeLlm):
            def complete(self, system, user, *, max_tokens=2000):
                raise LlmError("LLM HTTP 401: invalid key")

        tables: dict[str, FakeTable] = {}
        with self.assertRaisesRegex(PipelineIncomplete, "1 of 1"):
            run_pipeline("feed_digest", _deps(FakeRawItems([_row("sama", "200", 5, "x")]), tables, Broken()))
        run = tables["octp_pipeline_runs"].rows[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("invalid key", run["summary"]["errors"][0])
        self.assertEqual(tables["proc_feed_posts"].rows, [], "nothing is written when every translation fails")

    def test_a_missing_key_fails_the_run(self) -> None:
        def no_key(cfg):
            raise LlmError("KIMI_API_KEY is not set")

        tables: dict[str, FakeTable] = {}
        deps = _deps(FakeRawItems([_row("sama", "200", 5, "shipping")]), tables, FakeLlm())
        deps.llm = no_key
        with self.assertRaisesRegex(LlmError, "KIMI_API_KEY"):
            run_pipeline("feed_digest", deps)
        self.assertEqual(tables["octp_pipeline_runs"].rows[0]["status"], "failed")


class ChatClientTest(unittest.TestCase):
    def _client(self, handler) -> ChatClient:
        with patch.dict(os.environ, {"KIMI_API_KEY": "k"}):
            return ChatClient.from_config(
                {"model": "kimi-k2.6", "extra_body": {"thinking": {"type": "disabled"}}},
                transport=httpx.MockTransport(handler),
            )

    def test_success_counts_usage_and_sends_extra_body(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            seen["auth"] = request.headers["Authorization"]
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"choices": [{"message": {"content": " 你好 "}}], "usage": {"prompt_tokens": 10, "completion_tokens": 3}})

        client = self._client(handler)
        self.assertEqual(client.complete("sys", "hi"), "你好")
        self.assertEqual(client.usage.to_dict(), {"calls": 1, "prompt_tokens": 10, "completion_tokens": 3})
        self.assertEqual(seen["thinking"], {"type": "disabled"})
        self.assertEqual(seen["model"], "kimi-k2.6")
        self.assertEqual(seen["auth"], "Bearer k")
        self.assertEqual(seen["url"], "https://api.moonshot.cn/v1/chat/completions")

    def test_errors_raise_and_rate_limits_retry(self) -> None:
        client = self._client(lambda r: httpx.Response(400, text="bad model"))
        with self.assertRaisesRegex(LlmError, "HTTP 400"):
            client.complete("s", "u")

        answers = iter([httpx.Response(429, text="slow down"), httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})])
        client = self._client(lambda r: next(answers))
        with patch("infra.gateways.llm.time.sleep"):
            self.assertEqual(client.complete("s", "u"), "ok")

        with patch.dict(os.environ, {"KIMI_API_KEY": ""}):
            with self.assertRaisesRegex(LlmError, "KIMI_API_KEY is not set"):
                ChatClient.from_config({"model": "m"})


class RawItemsReadTest(unittest.TestCase):
    def test_select_published_since_decodes_json_and_utc_datetimes(self) -> None:
        from infra.dao.raw_items import RawItemsDao

        class FakeDb:
            def __init__(self) -> None:
                self.calls = []

            def execute_query(self, sql, params=()):
                self.calls.append((sql, list(params)))
                return [{"id": "a", "extra": '{"tweet_id": "1"}', "context_content": None, "metrics": "",
                         "source_published_date": datetime(2026, 10, 16, 1, 0)}]

        db = FakeDb()
        rows = RawItemsDao(db).select_published_since(["x_feed_accounts"], NOW - timedelta(hours=24))
        sql, params = db.calls[0]
        self.assertIn("`sub_source_type` IN (%s)", sql)
        self.assertEqual(params, ["x_feed_accounts", datetime(2026, 10, 15, 6, 0)])
        self.assertEqual(rows[0]["extra"], {"tweet_id": "1"})
        self.assertIsNone(rows[0]["metrics"])
        self.assertEqual(rows[0]["source_published_date"], datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(RawItemsDao(db).select_by_ids([]), [])


class LayerBoundaryTest(unittest.TestCase):
    def test_crawler_and_processing_do_not_import_each_other(self) -> None:
        root = Path(__file__).resolve().parents[1]
        def imports(path: Path, package: str) -> bool:
            pattern = re.compile(rf"^\s*(from|import)\s+{package}(\.|\s|$)", re.M)
            return bool(pattern.search(path.read_text(encoding="utf-8")))

        for path in (root / "processing").rglob("*.py"):
            self.assertFalse(imports(path, "crawler"), path.name)
        for path in (root / "crawler").rglob("*.py"):
            self.assertFalse(imports(path, "processing"), path.name)


if __name__ == "__main__":
    unittest.main()
