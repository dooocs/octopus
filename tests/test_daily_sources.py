from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from crawler.core.runner import run_config
from crawler.sources.web_helpers import (
    SourceUnavailable,
    iter_json_objects,
    next_flight_text,
    recent_identities,
    remember_identities,
)
from processing.aha_daily_handoff import build_body, run_aha_daily_handoff, to_sync_row
from processing.core import Deps, PipelineConfig


class _Resp:
    def __init__(self, payload: object = None, status_code: int = 200, text: str | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _flight_page(*objects: dict) -> str:
    """A page shaped like a Next.js App Router response: records inside flight chunks."""
    stream = "0:[\"$\",\"div\",null," + json.dumps({"items": list(objects)}) + "]\n"
    half = len(stream) // 2
    chunks = [stream[:half], stream[half:]]  # records may straddle chunk boundaries
    scripts = "".join(f"<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>" for chunk in chunks)
    return f"<html><body>{scripts}</body></html>"


def _config(scraper: str, sub: str, name: str, input_value: dict, source_type: str = "PRODUCT", item_type: str = "product") -> dict:
    return {
        "id": f"cfg-{sub}",
        "scraper": scraper,
        "name": name,
        "enabled": True,
        "priority": 50,
        "source_type": source_type,
        "sub_source_type": sub,
        "item_type": item_type,
        "input_schema_version": 1,
        "input": input_value,
    }


def _inp(source=None, fetch=None, filters=None, enrich=None) -> dict:
    return {"source": source or {}, "fetch": fetch or {}, "filters": filters or {}, "enrich": enrich or []}


class WebHelpersTest(unittest.TestCase):
    def test_flight_objects_survive_chunk_boundaries(self) -> None:
        page = _flight_page({"slug": "a", "votes_count": 1, "launch_start": "x"}, {"slug": "b", "other": 1})
        found = list(iter_json_objects(next_flight_text(page), ["slug", "votes_count"]))
        self.assertEqual([o["slug"] for o in found], ["a"])

    def test_recent_identities_respect_ttl_and_exclude_same_day(self) -> None:
        state: dict = {}
        remember_identities(state, ["old"], "2026-10-01", 30)
        remember_identities(state, ["fresh"], "2026-10-20", 30)
        remember_identities(state, ["today"], "2026-10-26", 30)
        self.assertEqual(recent_identities(state, "2026-10-26", 7), {"fresh"})
        self.assertEqual(recent_identities(state, "2026-10-26", 30), {"old", "fresh"})


class ShowHnTest(unittest.TestCase):
    def test_keeps_top_by_points(self) -> None:
        hits = [
            {"objectID": "1", "title": "Show HN: Low", "url": "https://low.dev", "points": 25, "num_comments": 1, "created_at_i": 1792000000, "author": "a"},
            {"objectID": "2", "title": "Show HN: High", "url": None, "points": 140, "num_comments": 30, "created_at_i": 1792000000, "author": "b", "story_text": "<p>Hi HN</p>"},
        ]
        cfg = _config("show_hn", "show_hn", "Show HN", _inp(fetch={"window_hours": 24, "hits_per_page": 100, "limit": 1}, filters={"min_points": 20}))
        with patch("crawler.sources.show_hn.http_get", return_value=_Resp({"hits": hits})) as get:
            rows = run_config(cfg, "2026-10-27").rows
        self.assertIn("created_at_i>", get.call_args.kwargs["params"]["numericFilters"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Show HN: High")
        self.assertEqual(rows[0]["url"], "https://news.ycombinator.com/item?id=2")  # text post: HN page
        self.assertEqual(rows[0]["metrics"], {"score": 140, "comments": 30})

    def test_http_error_fails_the_task(self) -> None:
        cfg = _config("show_hn", "show_hn", "Show HN", _inp(fetch={"limit": 5}))
        with patch("crawler.sources.show_hn.http_get", return_value=_Resp({}, 503)):
            with self.assertRaises(SourceUnavailable):
                run_config(cfg, "2026-10-27")


class UneedTest(unittest.TestCase):
    def test_daily_ranking_with_product_details(self) -> None:
        ranking = {"period": "daily", "date": "2026-10-25", "ranking": [
            {"rank": 2, "name": "Second", "slug": "second", "description": "two", "url": "https://two.dev", "uneed_url": "https://www.uneed.best/tool/second", "vote_count": 27, "launch_date": "2026-10-25"},
            {"rank": 1, "name": "First", "slug": "first", "description": "one", "url": "https://one.dev", "uneed_url": "https://www.uneed.best/tool/first", "vote_count": 31, "launch_date": "2026-10-25"},
        ]}
        detail = {"rich_description": "<p>Longer story</p><ul><li>point</li></ul>", "tags": ["ai"]}

        def fake_get(url, **kwargs):
            if url.endswith("/trending"):
                self.assertEqual(kwargs["params"]["period"], "daily")
                return _Resp(ranking)
            return _Resp(detail)

        cfg = _config("uneed", "uneed", "Uneed", _inp(fetch={"day_offset": 1, "limit": 1}, enrich=[{"name": "product_details"}]))
        with patch("crawler.sources.uneed.http_get", side_effect=fake_get):
            rows = run_config(cfg, "2026-10-27").rows
        self.assertEqual([r["title"] for r in rows], ["First"])
        self.assertIn("Longer story", rows[0]["content"])
        self.assertEqual(rows[0]["extra"]["website"], "https://one.dev")

    def test_api_error_fails_the_task(self) -> None:
        cfg = _config("uneed", "uneed", "Uneed", _inp(fetch={"limit": 3}))
        with patch("crawler.sources.uneed.http_get", return_value=_Resp({"error": "x"}, 400)):
            with self.assertRaises(SourceUnavailable):
                run_config(cfg, "2026-10-27")


class DevHuntTest(unittest.TestCase):
    def _page(self) -> str:
        now = datetime.now(timezone.utc)
        week = {"launch_start": (now - timedelta(days=2)).isoformat(), "launch_end": (now + timedelta(days=4)).isoformat()}
        past = {"launch_start": (now - timedelta(days=30)).isoformat(), "launch_end": (now - timedelta(days=24)).isoformat()}
        return _flight_page(
            {"id": 1, "slug": "leader", "name": "Leader", "slogan": "Top tool", "votes_count": 60, **week, "description": "$20"},
            {"id": 2, "slug": "runner", "name": "Runner", "slogan": "Second tool", "votes_count": 40, **week},
            {"id": 3, "slug": "old-winner", "name": "Old", "slogan": "Past", "votes_count": 99, **past},
        )

    def test_current_week_top_pick_skips_already_sent(self) -> None:
        cfg = _config("devhunt", "devhunt", "DevHunt", _inp(fetch={"limit": 1, "repeat_ttl_days": 7}, filters={"min_votes": 5}))
        page = self._page()
        with patch("crawler.sources.devhunt.http_get", return_value=_Resp(text=page)):
            first = run_config(cfg, "2026-10-26")
            self.assertEqual([r["title"] for r in first.rows], ["Leader"])
            self.assertEqual(first.rows[0]["content"], "Top tool")  # flight "$" refs are not content
            second = run_config(cfg, "2026-10-27", state=first.state)
        self.assertEqual([r["title"] for r in second.rows], ["Runner"])

    def test_page_without_records_fails(self) -> None:
        cfg = _config("devhunt", "devhunt", "DevHunt", _inp(fetch={"limit": 1}))
        with patch("crawler.sources.devhunt.http_get", return_value=_Resp(text="<html></html>")):
            with self.assertRaises(SourceUnavailable):
                run_config(cfg, "2026-10-27")


class MicroLaunchTest(unittest.TestCase):
    def test_current_batch_live_only(self) -> None:
        page = _flight_page(
            {"slug": "fastio", "codename": "Fastio", "votes_week": 10, "score_week": 22.2, "votes": 10, "batch_num": 202610, "status": "live", "start_date": "2026-10-22", "url": "https://fast.io/", "problem_label": "Files", "description": "$1a"},
            {"slug": "old", "codename": "Old", "votes_week": 50, "score_week": 90, "votes": 50, "batch_num": 202609, "status": "live", "start_date": "2026-09-02"},
            {"slug": "off", "codename": "Off", "votes_week": 30, "score_week": 60, "votes": 30, "batch_num": 202610, "status": "off", "start_date": "2026-10-02"},
        )
        cfg = _config("microlaunch", "microlaunch", "MicroLaunch", _inp(fetch={"limit": 1, "repeat_ttl_days": 30}, filters={"min_score_week": 5}))
        with patch("crawler.sources.microlaunch.http_get", return_value=_Resp(text=page)):
            rows = run_config(cfg, "2026-10-27").rows
        self.assertEqual([r["url"] for r in rows], ["https://microlaunch.net/p/fastio"])
        self.assertEqual(rows[0]["content"], "Files")


class TrustMrrTest(unittest.TestCase):
    def test_fastest_growing_verified_startup(self) -> None:
        page = _flight_page(
            {"_id": "1", "slug": "small", "name": "Small", "currentMrr": 900, "cachedGrowthMRR30d": 400.0},
            {"_id": "5", "slug": "just-connected", "name": "New", "currentMrr": 7304, "cachedGrowthMRR30d": 365100.0},
            {"_id": "2", "slug": "steady", "name": "Steady", "currentMrr": 50000, "cachedGrowthMRR30d": 3.0},
            {"_id": "3", "slug": "rocket", "name": "Rocket", "currentMrr": 24000, "cachedGrowthMRR30d": 100.0, "description": "SOC 2 fast", "founder": {"name": "Lewis", "handle": "lewis"}, "currentLast30DaysRevenue": 738124.09},
            {"_id": "4", "slug": "hidden", "name": "Hidden", "currentMrr": 99999, "cachedGrowthMRR30d": 90.0, "stealthMode": True},
        )
        cfg = _config("trustmrr", "trustmrr", "TrustMRR", _inp(fetch={"limit": 1, "repeat_ttl_days": 30}, filters={"min_mrr": 5000, "min_prior_mrr": 1000, "skip_stealth": True}), "STARTUP", "startup")
        with patch("crawler.sources.trustmrr.http_get", return_value=_Resp(text=page)):
            first = run_config(cfg, "2026-10-26")
            second = run_config(cfg, "2026-10-27", state=first.state)
        row = first.rows[0]
        self.assertEqual(row["url"], "https://trustmrr.com/startup/rocket")
        self.assertEqual(row["title"], "Rocket: $24,000 MRR, +$12,000 (+100%) in 30 days")  # beats Steady's +$1,456
        self.assertEqual(row["metrics"]["mrr_display"], "$24,000")
        self.assertIn("创始人 Lewis（X @lewis）", row["content"])
        self.assertEqual([r["extra"]["slug"] for r in second.rows], ["steady"])


REDDIT_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/first</name></author><content type="html">&lt;div class="md"&gt;&lt;p&gt;Hello &lt;strong&gt;world&lt;/strong&gt;&lt;/p&gt;&lt;/div&gt; submitted by &lt;a&gt;/u/first&lt;/a&gt; [link] [comments]</content>
<link href="https://www.reddit.com/r/LocalLLaMA/comments/abc123/top_post/"/><title>Top post</title><updated>2026-10-26T10:00:00+00:00</updated></entry>
<entry><author><name>/u/second</name></author><content type="html">second</content>
<link href="https://www.reddit.com/r/LocalLLaMA/comments/def456/second/"/><title>Second post</title><updated>2026-10-26T09:00:00+00:00</updated></entry>
</feed>"""


@patch("crawler.sources.reddit.RSS_PAUSE_SECONDS", 0)
class RedditTest(unittest.TestCase):
    def _cfg(self) -> dict:
        return _config(
            "reddit", "reddit_r_localllama", "Reddit r/LocalLLaMA",
            _inp(
                source={"subreddit": "LocalLLaMA"},
                fetch={"max_retries": 1, "post_limit": 10, "keep_top": 1, "max_comments_to_keep": 0},
                filters={"min_score": 50, "skip_nsfw": True, "skip_stickied": True, "skip_discussion_below": 100, "skip_self_text_below": 0},
            ),
            "NEWS", "discussion",
        )

    @patch.dict("os.environ", {"REDDIT_CLIENT_ID": "", "REDDIT_CLIENT_SECRET": ""})
    def test_json_refused_falls_back_to_rss_and_keeps_top_one(self) -> None:
        def fake_get(url, **kwargs):
            if url.endswith(".rss"):
                return _Resp(text=REDDIT_RSS)
            return _Resp({}, 403)

        with patch("crawler.sources.reddit.http_get", side_effect=fake_get):
            rows = run_config(self._cfg(), "2026-10-27").rows
        self.assertEqual(len(rows), 1)  # RSS has no score, min_score must not drop it
        self.assertEqual(rows[0]["title"], "Top post")
        self.assertEqual(rows[0]["content"], "Hello world")
        self.assertEqual(rows[0]["extra"]["discover_via"], "rss_fallback")

    @patch.dict("os.environ", {"REDDIT_CLIENT_ID": "", "REDDIT_CLIENT_SECRET": ""})
    def test_both_refused_fails_the_task(self) -> None:
        with patch("crawler.sources.reddit.http_get", return_value=_Resp({}, 403)):
            with self.assertRaises(SourceUnavailable):
                run_config(self._cfg(), "2026-10-27")


class ProductHuntTest(unittest.TestCase):
    @patch.dict("os.environ", {"PRODUCTHUNT_TOKEN": ""})
    def test_missing_token_fails_the_task(self) -> None:
        cfg = _config("product_hunt", "product_hunt", "Product Hunt", _inp(fetch={"max_retries": 1}, filters={"min_votes": 200}))
        with self.assertRaises(SourceUnavailable):
            run_config(cfg, "2026-10-27")

    @patch.dict("os.environ", {"PRODUCTHUNT_TOKEN": "t"})
    def test_api_error_fails_the_task(self) -> None:
        cfg = _config("product_hunt", "product_hunt", "Product Hunt", _inp(fetch={"max_retries": 1}, filters={"min_votes": 200}))
        with patch("crawler.sources.product_hunt.http_post", return_value=_Resp({"error": "bad"}, 401)):
            with self.assertRaises(SourceUnavailable):
                run_config(cfg, "2026-10-27")


class ProductHuntDayTest(unittest.TestCase):
    def test_launch_day_follows_daylight_saving(self) -> None:
        from crawler.sources.product_hunt import _pt_day_range
        # 2026-10-27 00:40 Beijing = 2026-10-26 09:40 PDT: yesterday is Oct 25, a PDT day.
        start, end = _pt_day_range(1, datetime(2026, 10, 26, 16, 40, tzinfo=timezone.utc))
        self.assertEqual((start, end), ("2026-10-25T07:00:00+00:00", "2026-10-26T07:00:00+00:00"))
        # After the switch back (Nov 1), a PST day starts at 08:00 UTC.
        start, _ = _pt_day_range(1, datetime(2026, 11, 3, 16, 40, tzinfo=timezone.utc))
        self.assertEqual(start, "2026-11-02T08:00:00+00:00")


class _Table:
    def __init__(self, existing=None) -> None:
        self.existing = existing or []
        self.upserts: list[tuple[list, str]] = []

    def select(self, params):
        return self.existing

    def upsert(self, rows, on_conflict):
        self.upserts.append((rows, on_conflict))

    def insert(self, payload):
        return {}

    def update(self, filters, payload):
        pass

    def delete(self, filters):
        pass


class _RawItems:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.calls = []

    def select_published_since(self, sub_source_types, since):
        return []

    def select_by_ids(self, ids):
        return []

    def select_by_snapshot_date(self, sub_source_types, snapshot_date):
        self.calls.append((list(sub_source_types), snapshot_date))
        return [r for r in self.rows if r["sub_source_type"] in sub_source_types]


class HandoffTest(unittest.TestCase):
    ROW = {
        "id": "abc", "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/top_post/", "title": "Top post",
        "source_type": "NEWS", "sub_source_type": "reddit_r_localllama", "item_type": "discussion",
        "author_id": "first", "author_url": "https://reddit.com/user/first",
        "source_published_date": datetime(2026, 10, 26, 10, tzinfo=timezone.utc), "snapshot_date": "2026-10-27",
        "metrics": {"score": 0, "comments": 0}, "content": "Hello world",
        "context_content": {"top_comments": [{"author": "x", "text": "Great"}]},
        "extra": {"subreddit": "LocalLLaMA"}, "scrape_config_snapshot": {"name": "Reddit r/LocalLLaMA"},
    }
    SOURCES = {"reddit_r_localllama": {"source_name": "Reddit r/LocalLLaMA", "source_type": "NEWS", "content_type": "reddit"}}

    def test_row_shape_matches_ahaindexsync(self) -> None:
        row = to_sync_row(self.ROW, self.SOURCES["reddit_r_localllama"], "2026-10-27")
        self.assertEqual(row["original_url"], self.ROW["url"])
        self.assertEqual(row["source_name"], "Reddit r/LocalLLaMA")
        self.assertEqual(row["content_type"], "reddit")
        self.assertEqual(row["scraper_slug"], "octopus:reddit_r_localllama")
        self.assertEqual(row["extra"]["source_tag"], "reddit_r_localllama")
        self.assertEqual(row["published_at"], "2026-10-26T10:00:00+00:00")
        self.assertIn("Top comments:\n- x: Great", build_body(self.ROW))

    def _run(self, existing):
        raw_table, content_table = _Table(existing), _Table()
        tables = {"raw_items": raw_table, "items_content": content_table}
        deps = Deps(
            raw_items=_RawItems([self.ROW]),
            table=lambda name: tables[name],
            llm=lambda cfg: None,
            now=lambda: datetime(2026, 10, 26, 16, 45, tzinfo=timezone.utc),  # 00:45 Beijing on the 27th
        )
        config = PipelineConfig(id="p", slug="aha_daily_handoff", name="x", kind="aha_daily_handoff", enabled=True, input={"sources": self.SOURCES})
        return run_aha_daily_handoff(config, deps), raw_table, content_table, deps

    def test_hands_off_todays_rows_in_beijing_date(self) -> None:
        summary, raw_table, content_table, deps = self._run([])
        self.assertEqual(deps.raw_items.calls, [(["reddit_r_localllama"], "2026-10-27")])
        self.assertEqual(summary["handed_off"], 1)
        self.assertEqual(raw_table.upserts[0][0][0]["snapshot_date"], "2026-10-27")
        self.assertEqual(content_table.upserts[0][0][0]["item_id"], "abc")

    def test_rows_owned_by_ahaindexsync_are_left_alone(self) -> None:
        summary, raw_table, _, _ = self._run([{"id": "abc", "scraper_slug": "hackernews"}])
        self.assertEqual(summary["handed_off"], 0)
        self.assertEqual(summary["skipped_owned_by_sync"], 1)
        self.assertEqual(raw_table.upserts[0][0], [])


if __name__ == "__main__":
    unittest.main()
