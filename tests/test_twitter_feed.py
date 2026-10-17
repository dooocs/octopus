from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx

from crawler.core.runner import run_config
from crawler.sources import twitter_feed
from crawler.sources.twitter import TwitterAdapter
from crawler.sources.twitter_feed import (
    KIND_ORIGINAL,
    KIND_QUOTE,
    KIND_REPLY,
    KIND_RETWEET,
    KIND_THREAD,
    TwitterFeedAdapter,
    account_batches,
    build_query,
    classify,
    clean_text,
    fetch_window,
)
from crawler.sources.twitterapi_io import TwitterApiError, check_response, media_of

NOW = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)  # 10:00 Beijing time
TWENTY_TWO = [
    "sama", "gdb", "karpathy", "AndrewYNg", "ylecun", "demishassabis", "ilyasut", "fchollet",
    "thom_wolf", "clem_delangue", "JeffDean", "drjimfan", "svpino", "elonmusk", "OpenAI",
    "AnthropicAI", "GoogleDeepMind", "MistralAI", "huggingface", "perplexity_ai", "xai", "MetaAI",
]


def _tweet(tweet_id: str, user: str, user_id: str, text: str = "hello", **extra) -> dict:
    return {
        "type": "tweet",
        "id": tweet_id,
        "url": f"https://x.com/{user}/status/{tweet_id}",
        "text": text,
        "createdAt": "Thu Oct 16 01:30:00 +0000 2026",
        "likeCount": 5,
        "retweetCount": 1,
        "replyCount": 0,
        "quoteCount": 0,
        "viewCount": 100,
        "lang": "en",
        "isReply": False,
        "conversationId": tweet_id,
        "author": {"userName": user, "id": user_id, "name": user.title(), "profilePicture": f"https://pbs.twimg.com/{user}_normal.jpg"},
        **extra,
    }


def _config(accounts=("karpathy", "sama"), enrich=None) -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "动态 · X 博主",
        "scraper": "twitter_feed",
        "enabled": True,
        "priority": 100,
        "source_type": "social",
        "sub_source_type": "x_feed_accounts",
        "item_type": "post",
        "input_schema_version": 1,
        "input": {
            "source": {"watch_accounts": list(accounts)},
            "fetch": {"max_lookback_hours": 24, "overlap_minutes": 5, "max_pages": 5},
            "filters": {},
            "enrich": enrich if enrich is not None else [],
        },
    }


class WindowTest(unittest.TestCase):
    def test_first_run_looks_back_the_whole_lookback(self) -> None:
        self.assertEqual(fetch_window({}, NOW, max_lookback_hours=24, overlap_minutes=5), (NOW - timedelta(hours=24), NOW))

    def test_next_run_starts_at_the_cursor_minus_the_overlap(self) -> None:
        state = {"feed_cursor": (NOW - timedelta(hours=1)).isoformat()}
        self.assertEqual(fetch_window(state, NOW, max_lookback_hours=24, overlap_minutes=5)[0], NOW - timedelta(minutes=65))

    def test_six_oclock_run_covers_the_night_from_the_one_oclock_cursor(self) -> None:
        six = datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc)  # 06:00 Beijing
        one = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)  # 01:00 Beijing
        since, until = fetch_window({"feed_cursor": one.isoformat()}, six, max_lookback_hours=24, overlap_minutes=5)
        self.assertEqual((since, until), (one - timedelta(minutes=5), six))

    def test_an_old_cursor_is_clamped_to_the_lookback(self) -> None:
        state = {"feed_cursor": "2026-10-01T00:00:00+00:00"}
        self.assertEqual(fetch_window(state, NOW, max_lookback_hours=24, overlap_minutes=5)[0], NOW - timedelta(hours=24))


class QueryTest(unittest.TestCase):
    def test_the_twenty_two_accounts_fit_in_few_queries_within_the_budget(self) -> None:
        batches = account_batches(TWENTY_TWO)
        self.assertLessEqual(len(batches), 2)
        self.assertEqual([a for batch in batches for a in batch], TWENTY_TWO)
        for batch in batches:
            self.assertLessEqual(len(" OR ".join(f"from:{a}" for a in batch)), twitter_feed.MAX_FROM_CLAUSE_CHARS)

    def test_query_is_bounded_by_unix_timestamps(self) -> None:
        query = build_query(["a", "b"], NOW - timedelta(hours=1), NOW)
        self.assertEqual(query, f"(from:a OR from:b) since_time:{int(NOW.timestamp()) - 3600} until_time:{int(NOW.timestamp())}")


class ClassifyTest(unittest.TestCase):
    def test_kinds(self) -> None:
        self.assertEqual(classify(_tweet("1", "a", "10")), KIND_ORIGINAL)
        self.assertEqual(classify(_tweet("2", "a", "10", retweeted_tweet={"id": "9"})), KIND_RETWEET)
        self.assertEqual(classify(_tweet("3", "a", "10", text="RT @b: hi")), KIND_RETWEET)
        self.assertEqual(classify(_tweet("4", "a", "10", quoted_tweet={"id": "9"})), KIND_QUOTE)
        self.assertEqual(classify(_tweet("5", "a", "10", isReply=True, inReplyToId="9", inReplyToUserId="20")), KIND_REPLY)
        self.assertEqual(classify(_tweet("6", "a", "10", isReply=True, inReplyToId="1", inReplyToUserId="10", conversationId="1")), KIND_THREAD)

    def test_media_is_read_from_extended_entities(self) -> None:
        raw = _tweet("1", "a", "10", extendedEntities={"media": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/x.jpg", "original_info": {"width": 800, "height": 600}},
            {"type": "video", "media_url_https": "https://pbs.twimg.com/cover.jpg"},
        ]})
        self.assertEqual([(m.type, m.url, m.width) for m in media_of(raw)], [
            ("photo", "https://pbs.twimg.com/media/x.jpg", 800),
            ("video", "https://pbs.twimg.com/cover.jpg", None),
        ])


class CleanTextTest(unittest.TestCase):
    """Shapes copied from live twitterapi.io responses (2026-10-17)."""

    def test_own_media_link_is_removed_and_links_are_expanded(self) -> None:
        raw = {
            "text": "Gemini 4 Argon sets a new record.\n\nDetails: https://t.co/aaa https://t.co/med",
            "entities": {"urls": [{"url": "https://t.co/aaa", "expanded_url": "https://blog.example.com/argon", "display_url": "blog.example.com/argon"}]},
            "extendedEntities": {"media": [{"url": "https://t.co/med", "type": "photo", "media_url_https": "https://pbs.twimg.com/media/x.jpg"}]},
        }
        text, links = clean_text(raw)
        self.assertEqual(text, "Gemini 4 Argon sets a new record.\n\nDetails: https://blog.example.com/argon")
        self.assertEqual(links, [{"url": "https://blog.example.com/argon", "display_url": "blog.example.com/argon"}])

    def test_long_posts_are_not_cut_at_display_text_range(self) -> None:
        body = "x" * 700
        raw = {"text": f"{body} https://t.co/vid", "displayTextRange": [0, 275],
               "extendedEntities": {"media": [{"url": "https://t.co/vid", "type": "video"}]}}
        self.assertEqual(clean_text(raw)[0], body)

    def test_media_only_post_has_empty_text_and_unknown_tco_is_kept(self) -> None:
        self.assertEqual(clean_text({"text": "https://t.co/med", "extendedEntities": {"media": [{"url": "https://t.co/med"}]}}), ("", []))
        self.assertEqual(clean_text({"text": "see https://t.co/zzz"})[0], "see https://t.co/zzz")


class _Api:
    """httpx transport standing in for twitterapi.io."""

    def __init__(self, pages: list[dict] | None = None, status: int = 200, body: dict | None = None) -> None:
        self.pages = pages or []
        self.status = status
        self.body = body
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query, keep_blank_values=True).items()}
        self.calls.append({"path": request.url.path, "params": params, "key": request.headers.get("X-API-Key")})
        if self.body is not None or self.status != 200:
            return httpx.Response(self.status, json=self.body or {})
        index = 0 if not params.get("cursor") else int(params["cursor"])
        return httpx.Response(200, json=self.pages[index])


class FeedRunTest(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"TWITTERAPI_IO_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)
        clock = patch("crawler.sources.twitter_feed._utcnow", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.addCleanup(setattr, TwitterFeedAdapter, "transport", None)

    def _run(self, api: _Api, state=None, config=None):
        TwitterFeedAdapter.transport = httpx.MockTransport(api)
        return run_config(config or _config(), "2026-10-16", state=state or {})

    def test_keeps_originals_and_own_threads_across_pages(self) -> None:
        pages = [
            {"status": "success", "tweets": [
                _tweet("100", "karpathy", "1", text="new model notes https://t.co/lnk https://t.co/pic",
                       entities={"urls": [{"url": "https://t.co/lnk", "expanded_url": "https://karpathy.ai/notes", "display_url": "karpathy.ai/notes"}]},
                       extendedEntities={"media": [
                    {"type": "photo", "url": "https://t.co/pic", "media_url_https": "https://pbs.twimg.com/media/a.jpg"}]}),
                _tweet("101", "karpathy", "1", text="2/ more", isReply=True, inReplyToId="100", inReplyToUserId="1", conversationId="100"),
                _tweet("102", "sama", "2", text="RT @x: hi", retweeted_tweet={"id": "5"}),
            ], "has_next_page": True, "next_cursor": "1"},
            {"status": "success", "tweets": [
                _tweet("103", "sama", "2", text="look at this", quoted_tweet={"id": "6"}),
                _tweet("104", "sama", "2", text="@someone thanks", isReply=True, inReplyToId="7", inReplyToUserId="99"),
                _tweet("105", "Sama", "2", text="shipping today"),
                _tweet("106", "notwatched", "3", text="not ours"),
            ], "has_next_page": False, "next_cursor": ""},
        ]
        api = _Api(pages)
        result = self._run(api, state={"feed_cursor": (NOW - timedelta(hours=1)).isoformat()})

        ids = sorted(row["extra"]["tweet_id"] for row in result.rows)
        self.assertEqual(ids, ["100", "101", "105"])
        self.assertEqual(result.items_discovered, 6)  # the unwatched account is dropped in discover
        thread = next(r for r in result.rows if r["extra"]["tweet_id"] == "101")
        self.assertEqual(thread["context_content"]["thread"], {"conversation_id": "100", "is_root": False, "in_reply_to_id": "100"})
        root = next(r for r in result.rows if r["extra"]["tweet_id"] == "100")
        self.assertTrue(root["context_content"]["thread"]["is_root"])
        self.assertEqual(root["context_content"]["media"][0]["source_url"], "https://pbs.twimg.com/media/a.jpg")
        self.assertEqual(root["content"], "new model notes https://karpathy.ai/notes", "t.co resolved, own photo link removed")
        self.assertEqual(root["title"], "new model notes https://karpathy.ai/notes")
        self.assertEqual(root["context_content"]["links"], [{"url": "https://karpathy.ai/notes", "display_url": "karpathy.ai/notes"}])
        self.assertEqual(root["url"], "https://x.com/karpathy/status/100")
        self.assertEqual(root["author_id"], "karpathy")
        self.assertEqual(root["sub_source_type"], "x_feed_accounts")
        self.assertEqual(root["source_published_date"], "2026-10-16T01:30:00+00:00")

        # One query for both accounts, bounded by the window, paged by cursor.
        self.assertEqual(len(api.calls), 2)
        self.assertEqual(api.calls[0]["path"], "/twitter/tweet/advanced_search")
        self.assertEqual(api.calls[0]["key"], "test-key")
        since = int((NOW - timedelta(minutes=65)).timestamp())
        self.assertEqual(api.calls[0]["params"]["query"], f"(from:karpathy OR from:sama) since_time:{since} until_time:{int(NOW.timestamp())}")
        self.assertEqual(api.calls[1]["params"]["cursor"], "1")
        # The cursor moves to the end of this window (persisted by the runtime on success).
        self.assertEqual(result.state["feed_cursor"], NOW.isoformat())

    def test_media_archive_replaces_sources_with_oss_urls_and_skips_failures(self) -> None:
        pages = [{"status": "success", "tweets": [
            _tweet("200", "karpathy", "1", extendedEntities={"media": [
                {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/ok.jpg"},
                {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/broken.jpg"},
            ]}),
        ], "has_next_page": False}]

        def fake_archive(url: str, prefix: str):
            if "broken" in url:
                return None
            return {"url": f"https://www.amazingindex.com/{prefix}/{url.rsplit('/', 1)[1]}", "key": f"{prefix}/x"}

        with patch("crawler.sources.twitter_feed.archive_media", side_effect=fake_archive) as archive:
            result = self._run(_Api(pages), config=_config(enrich=[{"name": "media_archive"}]))
        row = result.rows[0]
        self.assertEqual([m["url"] for m in row["context_content"]["media"]], ["https://www.amazingindex.com/media/x/ok.jpg"])
        self.assertEqual(row["context_content"]["media"][0]["source_url"], "https://pbs.twimg.com/media/ok.jpg")
        self.assertEqual(row["extra"]["avatar_url"], "https://www.amazingindex.com/media/x/avatars/karpathy_400x400.jpg")
        self.assertIn(("https://pbs.twimg.com/karpathy_400x400.jpg", "media/x/avatars"), [c.args for c in archive.call_args_list])

    def test_out_of_credits_fails_the_task_instead_of_returning_nothing(self) -> None:
        with self.assertRaisesRegex(TwitterApiError, "out of credits"):
            self._run(_Api(status=402, body={"status": "error", "msg": "Credits is not enough"}))
        with self.assertRaisesRegex(TwitterApiError, "out of credits"):
            self._run(_Api(body={"status": "error", "msg": "Insufficient balance, please recharge"}))
        with self.assertRaisesRegex(TwitterApiError, "rejected the API key"):
            self._run(_Api(status=401, body={"error": "unauthorized"}))

    def test_missing_key_fails(self) -> None:
        with patch.dict(os.environ, {"TWITTERAPI_IO_KEY": ""}):
            with self.assertRaisesRegex(TwitterApiError, "TWITTERAPI_IO_KEY is not set"):
                self._run(_Api([{"status": "success", "tweets": []}]))

    def test_an_empty_window_is_a_success_and_still_moves_the_cursor(self) -> None:
        result = self._run(_Api([{"status": "success", "tweets": [], "has_next_page": False}]))
        self.assertEqual(result.rows, [])
        self.assertEqual(result.state["feed_cursor"], NOW.isoformat())


class CheckResponseTest(unittest.TestCase):
    def test_retryable_and_fatal_statuses(self) -> None:
        from crawler.sources.twitterapi_io import TwitterApiRetryableError

        with self.assertRaises(TwitterApiRetryableError):
            check_response(429, {}, "")
        with self.assertRaises(TwitterApiRetryableError):
            check_response(503, {}, "")
        with self.assertRaises(TwitterApiError):
            check_response(400, {"msg": "bad query"}, "")
        self.assertEqual(check_response(200, {"tweets": []}), {"tweets": []})


class DailyTwitterAdapterTest(unittest.TestCase):
    """The daily twitter_twscrape adapter no longer turns failures into an empty result."""

    def test_api_errors_propagate(self) -> None:
        async def boom(self, endpoint, params):
            raise TwitterApiError("twitterapi.io account is out of credits (HTTP 402)")

        config = {
            "id": None, "name": "X AI Leaders", "scraper": "twitter_twscrape", "enabled": True, "priority": 100,
            "source_type": "social", "sub_source_type": "x_ai_leaders", "item_type": "post", "input_schema_version": 1,
            "input": {"source": {"watch_accounts": ["karpathy"], "tracked_keywords": []}, "fetch": {"max_age_days": 2},
                      "filters": {"timeline_min_faves": 0}, "enrich": []},
        }
        with patch.dict(os.environ, {"TWITTERAPI_IO_KEY": "k"}), patch.object(TwitterAdapter, "_request_with_retry", boom):
            with self.assertRaisesRegex(TwitterApiError, "out of credits"):
                run_config(config, "2026-10-16")
        with patch.dict(os.environ, {"TWITTERAPI_IO_KEY": ""}):
            with self.assertRaisesRegex(TwitterApiError, "not set"):
                run_config(config, "2026-10-16")


if __name__ == "__main__":
    unittest.main()
