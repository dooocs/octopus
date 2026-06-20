from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from core.contracts import RunContext, ScraperConfig
from core.registry import get_adapter
from core.runner import run_config


class _FakeResponse:
    def __init__(self, payload: object | None = None, *, text: str = "", status_code: int = 200) -> None:
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class EnrichPhaseBoundaryTest(unittest.TestCase):
    def test_legacy_adapter_runs_full_text_only_in_enrich_phase(self) -> None:
        rss_text = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <item>
      <title>Demo article</title>
      <link>https://example.com/article</link>
      <description>Feed summary</description>
    </item>
  </channel>
</rss>
"""
        config_row = {
            "type": "rss",
            "name": "Feed",
            "enabled": True,
            "source_type": "ARTICLE",
            "sub_source_type": "feed",
            "item_type": "article",
            "config": {
                "source": {"url": "https://example.com/feed.xml"},
                "fetch": {"max_items": 1, "fetch_window_hours": 999999, "fetch_full_text": True},
                "filters": {},
                "enrich": [{"name": "full_text", "when": "always"}],
                "runtime": {},
            },
        }
        adapter = get_adapter("rss")()
        config = ScraperConfig.from_mapping(config_row)
        ctx = RunContext(snapshot_date="2026-06-20")

        with patch("scrapers.rss_feed.http_get", return_value=_FakeResponse(text=rss_text)), patch(
            "scrapers.rss_feed._fetch_full_text",
            return_value="Full article text",
        ) as fetch_full_text:
            records = adapter.discover(ctx, config)
            self.assertEqual(records[0].content, "Feed summary")
            self.assertEqual(records[0].context_content["full_text_fetched"], False)
            fetch_full_text.assert_not_called()

            enriched = adapter.enrich(ctx, records, config)

        self.assertEqual(enriched[0].content, "Full article text")
        self.assertTrue(enriched[0].context_content["full_text_fetched"])
        fetch_full_text.assert_called_once()

    def test_native_adapter_runs_lobsters_comments_only_in_enrich_phase(self) -> None:
        feed_payload = [
            {
                "short_id": "abc123",
                "created_at": "2026-06-19T10:00:00.000-05:00",
                "title": "Lobsters story",
                "url": "https://example.com/story",
                "score": 10,
                "flags": 0,
                "comment_count": 2,
                "description_plain": "",
                "submitter_user": "alice",
                "tags": ["programming"],
            }
        ]
        detail_payload = {
            "comments": [
                {"short_id": "high", "comment": "<p>High</p>", "score": 9, "commenting_user": "high_user"},
            ]
        }
        seen_urls: list[str] = []

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            seen_urls.append(url)
            if url.endswith("/hottest.json"):
                return _FakeResponse(feed_payload)
            return _FakeResponse(detail_payload)

        config_row = {
            "type": "lobsters",
            "name": "Lobsters",
            "enabled": True,
            "source_type": "NEWS",
            "sub_source_type": "lobsters_top",
            "item_type": "article",
            "config": {
                "source": {"feed": "hottest", "tags": []},
                "fetch": {"window_days": 7, "limit": 1, "comments_to_keep": 10},
                "filters": {"min_score": 0, "min_comments": 0, "tag_whitelist": [], "tag_blacklist": []},
                "enrich": [{"name": "top_comments", "when": "always"}],
                "runtime": {},
            },
        }
        adapter = get_adapter("lobsters")()
        config = ScraperConfig.from_mapping(config_row)
        ctx = RunContext(snapshot_date="2026-06-20")

        with patch("sources.lobsters.http_get", side_effect=fake_get):
            records = adapter.discover(ctx, config)
            self.assertEqual(records[0].context_content["top_comments"], [])
            self.assertEqual(seen_urls, ["https://lobste.rs/hottest.json"])

            enriched = adapter.enrich(ctx, records, config)

        self.assertEqual(enriched[0].context_content["top_comments"][0]["id"], "high")
        self.assertIn("https://lobste.rs/s/abc123.json", seen_urls)


class HackerNewsEnrichmentTest(unittest.TestCase):
    def test_hackernews_fetches_story_text_article_body_and_top_comments(self) -> None:
        now_ts = int(time.time())

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            if url.endswith("/newstories.json"):
                return _FakeResponse([100])
            if url.endswith("/item/100.json"):
                return _FakeResponse(
                    {
                        "id": 100,
                        "type": "story",
                        "title": "HN story",
                        "url": "https://example.com/article",
                        "text": "<p>HN post body</p>",
                        "by": "alice",
                        "time": now_ts,
                        "score": 123,
                        "descendants": 2,
                        "kids": [200, 201],
                    }
                )
            if url.endswith("/item/200.json"):
                return _FakeResponse({"id": 200, "text": "<p>Great comment</p>", "by": "bob", "time": now_ts, "kids": []})
            if url.endswith("/item/201.json"):
                return _FakeResponse({"id": 201, "text": "<p>Second comment</p>", "by": "carol", "time": now_ts, "kids": []})
            return _FakeResponse(text="<html><article>Original article body</article></html>")

        config = {
            "type": "hackernews",
            "name": "HN",
            "enabled": True,
            "source_type": "NEWS",
            "sub_source_type": "hackernews",
            "item_type": "article",
            "config": {
                "source": {"feed": "newstories"},
                "fetch": {
                    "new_n": 1,
                    "cutoff_hours": 999999,
                    "fetch_workers": 1,
                    "skip_domains": [],
                    "max_comments_to_fetch": 10,
                    "max_comments_to_keep": 10,
                },
                "filters": {"min_score": 1},
                "enrich": [
                    {"name": "article_body", "when": "has_external_url"},
                    {"name": "top_comments", "when": "always"},
                ],
                "runtime": {},
            },
        }

        with patch("scrapers.hackernews.http_get", side_effect=fake_get), patch(
            "scrapers.hackernews.trafilatura.extract",
            return_value="Original article body",
        ):
            result = run_config(config, "2026-06-20")

        self.assertEqual(len(result.rows), 1)
        row = result.rows[0]
        self.assertEqual(row["content"], "Original article body")
        self.assertEqual(row["context_content"]["hn_post_text"], "HN post body")
        self.assertEqual(row["context_content"]["original_content"], "Original article body")
        self.assertEqual([c["author"] for c in row["context_content"]["top_comments"]], ["bob", "carol"])


class FeedFullTextEnrichmentTest(unittest.TestCase):
    def test_rss_full_text_overrides_feed_summary_and_preserves_summary_context(self) -> None:
        rss_text = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <item>
      <title>Demo article</title>
      <link>https://example.com/article</link>
      <description>Feed summary</description>
    </item>
  </channel>
</rss>
"""
        config = {
            "type": "rss",
            "name": "Feed",
            "enabled": True,
            "source_type": "ARTICLE",
            "sub_source_type": "feed",
            "item_type": "article",
            "config": {
                "source": {"url": "https://example.com/feed.xml"},
                "fetch": {
                    "max_items": 1,
                    "fetch_window_hours": 999999,
                    "fetch_full_text": True,
                    "full_text_timeout": 5,
                    "max_content_chars": 12000,
                },
                "filters": {},
                "enrich": [{"name": "full_text", "when": "always"}],
                "runtime": {},
            },
        }

        with patch("scrapers.rss_feed.http_get", return_value=_FakeResponse(text=rss_text)), patch(
            "scrapers.rss_feed._fetch_full_text",
            return_value="Full article text",
        ):
            result = run_config(config, "2026-06-20")

        self.assertEqual(result.rows[0]["content"], "Full article text")
        self.assertEqual(result.rows[0]["context_content"]["feed_summary"], "Feed summary")
        self.assertTrue(result.rows[0]["context_content"]["full_text_fetched"])


class AIBlogFullTextEnrichmentTest(unittest.TestCase):
    def test_ai_blog_fetches_full_article_text_from_list_page(self) -> None:
        list_html = """
<html><body>
  <a href="/news/demo"><h2>Demo news</h2><p>List summary</p><time datetime="2026-06-20T00:00:00Z"></time></a>
</body></html>
"""
        config = {
            "type": "ai_blog",
            "name": "AI Blog",
            "enabled": True,
            "source_type": "website",
            "sub_source_type": "ai_blog",
            "item_type": "article",
            "config": {
                "source": {"base_url": "https://example.com", "news_url": "https://example.com/news", "link_selector": "a"},
                "fetch": {"fetch_window_hours": 999999, "fetch_full_text": True, "full_text_timeout": 5, "max_content_chars": 12000},
                "filters": {},
                "enrich": [{"name": "full_text", "when": "always"}],
                "runtime": {},
            },
        }

        with patch("scrapers.ai_blog.http_get", return_value=_FakeResponse(text=list_html)), patch(
            "scrapers.ai_blog._fetch_full_text",
            return_value="Full blog article",
        ):
            result = run_config(config, "2026-06-20")

        self.assertEqual(result.rows[0]["content"], "Full blog article")
        self.assertEqual(result.rows[0]["context_content"]["list_summary"], "List summary")
        self.assertTrue(result.rows[0]["context_content"]["full_text_fetched"])


class V2EXEnrichmentTest(unittest.TestCase):
    def test_v2ex_fetches_post_body_and_top_replies_by_thanked(self) -> None:
        topic = {
            "id": 1,
            "url": "https://www.v2ex.com/t/1",
            "title": "V2EX topic",
            "content_rendered": "<p>Post body</p>",
            "replies": 2,
            "created": int(time.time()),
            "member": {"username": "alice"},
            "node": {"title": "AI"},
        }
        replies = [
            {"id": 11, "member": {"username": "low"}, "content_rendered": "<p>Low</p>", "thanked": 1},
            {"id": 12, "member": {"username": "high"}, "content_rendered": "<p>High</p>", "thanked": 9},
        ]

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            if url.endswith("/api/topics/hot.json"):
                return _FakeResponse([topic])
            if url.endswith("/api/replies/show.json"):
                return _FakeResponse(replies)
            return _FakeResponse(text="<html>999 次点击</html>")

        config = {
            "type": "community_v2ex",
            "name": "V2EX",
            "enabled": True,
            "source_type": "community",
            "sub_source_type": "v2ex_hot",
            "item_type": "discussion",
            "config": {
                "source": {},
                "fetch": {"top_n": 1, "top_clicked_limit": 1, "max_replies_to_fetch": 10, "max_replies_to_keep": 10},
                "filters": {},
                "enrich": [{"name": "top_replies", "when": "always"}],
                "runtime": {},
            },
        }

        with patch("scrapers.community_v2ex.http_get", side_effect=fake_get):
            result = run_config(config, "2026-06-20")

        row = result.rows[0]
        self.assertEqual(row["metrics"]["clicks"], 999)
        self.assertEqual(row["context_content"]["post_content"], "Post body")
        self.assertEqual([reply["author"] for reply in row["context_content"]["top_comments"]], ["high", "low"])


class LinuxDoEnrichmentTest(unittest.TestCase):
    def test_linuxdo_fetches_post_body_and_top_comments_by_likes(self) -> None:
        top_payload = {
            "topic_list": {
                "topics": [
                    {
                        "id": 100,
                        "slug": "demo",
                        "title": "LinuxDo topic",
                        "posts_count": 3,
                        "views": 20,
                        "like_count": 2,
                        "created_at": "2026-06-20T00:00:00Z",
                    }
                ]
            }
        }
        topic_payload = {
            "post_stream": {
                "posts": [
                    {"id": 1, "raw": "Original post"},
                    {"id": 2, "raw": "Low comment", "username": "low", "like_count": 1, "score": 1},
                    {"id": 3, "raw": "High comment", "username": "high", "like_count": 8, "score": 2},
                ]
            }
        }

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            if url.endswith("/top.json?period=daily"):
                return _FakeResponse(top_payload)
            return _FakeResponse(topic_payload)

        config = {
            "type": "community_linuxdo",
            "name": "LinuxDo",
            "enabled": True,
            "source_type": "community",
            "sub_source_type": "linuxdo_daily",
            "item_type": "discussion",
            "config": {
                "source": {},
                "fetch": {"top_n": 1, "limit": 1, "max_replies_to_fetch": 10, "max_replies_to_keep": 10},
                "filters": {},
                "enrich": [{"name": "top_replies", "when": "always"}],
                "runtime": {},
            },
        }

        with patch("scrapers.community_linuxdo.http_get", side_effect=fake_get):
            result = run_config(config, "2026-06-20")

        row = result.rows[0]
        self.assertEqual(row["context_content"]["post_content"], "Original post")
        self.assertEqual([comment["author"] for comment in row["context_content"]["top_comments"]], ["high", "low"])


class ProductHuntEnrichmentTest(unittest.TestCase):
    def test_product_hunt_preserves_graphql_comments(self) -> None:
        payload = {
            "data": {
                "posts": {
                    "edges": [
                        {
                            "node": {
                                "id": "ph1",
                                "name": "Demo Product",
                                "tagline": "Tagline",
                                "description": "Description",
                                "url": "https://producthunt.com/posts/demo",
                                "website": "https://example.com",
                                "votesCount": 300,
                                "commentsCount": 1,
                                "createdAt": "2026-06-20T00:00:00Z",
                                "topics": {"edges": [{"node": {"name": "AI", "slug": "artificial-intelligence"}}]},
                                "makers": [{"name": "Maker", "username": "maker"}],
                                "comments": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "c1",
                                                "body": "Useful launch",
                                                "createdAt": "2026-06-20T01:00:00Z",
                                                "user": {"name": "User", "username": "user"},
                                            }
                                        }
                                    ]
                                },
                            }
                        }
                    ]
                }
            }
        }
        config = {
            "type": "product_hunt",
            "name": "PH",
            "enabled": True,
            "source_type": "product_platform",
            "sub_source_type": "product_hunt",
            "item_type": "product",
            "config": {
                "source": {},
                "fetch": {"max_retries": 1},
                "filters": {"min_votes": 1, "topic_whitelist": [], "topic_blacklist": []},
                "enrich": [{"name": "product_comments", "when": "always"}],
                "runtime": {},
            },
        }

        with patch.dict("os.environ", {"PRODUCTHUNT_TOKEN": "token"}), patch(
            "scrapers.product_hunt.http_post",
            return_value=_FakeResponse(payload),
        ):
            result = run_config(config, "2026-06-20")

        self.assertEqual(result.rows[0]["context_content"]["top_comments"][0]["text"], "Useful launch")


class RedditEnrichmentTest(unittest.TestCase):
    def test_reddit_fetches_top_comments(self) -> None:
        top_payload = {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "abc",
                            "title": "Reddit post",
                            "permalink": "/r/LocalLLaMA/comments/abc/reddit_post/",
                            "score": 500,
                            "num_comments": 2,
                            "over_18": False,
                            "stickied": False,
                            "link_flair_text": "",
                            "is_self": True,
                            "selftext": "Self text body",
                            "created_utc": int(time.time()),
                            "author": "alice",
                        }
                    }
                ]
            }
        }
        comments_payload = [
            {"data": {"children": []}},
            {
                "data": {
                    "children": [
                        {"kind": "t1", "data": {"id": "c1", "author": "bob", "body": "Top comment", "score": 10}},
                        {"kind": "t1", "data": {"id": "c2", "author": "carol", "body": "Second comment", "score": 5}},
                    ]
                }
            },
        ]

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            if url.endswith("/top.json"):
                return _FakeResponse(top_payload)
            return _FakeResponse(comments_payload)

        config = {
            "type": "reddit",
            "name": "Reddit",
            "enabled": True,
            "source_type": "community",
            "sub_source_type": "reddit_top",
            "item_type": "discussion",
            "config": {
                "source": {"subreddit": "LocalLLaMA"},
                "fetch": {"max_retries": 1, "post_limit": 1, "max_comments_to_keep": 10},
                "filters": {"min_score": 1, "skip_nsfw": True, "skip_stickied": True, "skip_discussion_below": 0, "skip_self_text_below": 0},
                "enrich": [{"name": "top_comments", "when": "always"}],
                "runtime": {},
            },
        }

        with patch.dict("os.environ", {"REDDIT_CLIENT_ID": "", "REDDIT_CLIENT_SECRET": ""}), patch(
            "scrapers.reddit.http_get",
            side_effect=fake_get,
        ):
            result = run_config(config, "2026-06-20")

        self.assertEqual(result.rows[0]["context_content"]["comments_fetch_status"], "ok")
        self.assertEqual([comment["author"] for comment in result.rows[0]["context_content"]["top_comments"]], ["bob", "carol"])

    def test_reddit_uses_oauth_when_credentials_are_available(self) -> None:
        token_payload = {"access_token": "access-token"}
        top_payload = {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "oauth1",
                            "title": "OAuth Reddit post",
                            "permalink": "/r/LocalLLaMA/comments/oauth1/oauth_reddit_post/",
                            "score": 500,
                            "num_comments": 1,
                            "over_18": False,
                            "stickied": False,
                            "link_flair_text": "",
                            "is_self": False,
                            "domain": "example.com",
                            "url": "https://example.com/post",
                            "created_utc": int(time.time()),
                            "author": "alice",
                        }
                    }
                ]
            }
        }
        comments_payload = [
            {"data": {"children": []}},
            {"data": {"children": [{"kind": "t1", "data": {"id": "c1", "author": "bob", "body": "OAuth comment", "score": 10}}]}},
        ]

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            self.assertIn("oauth.reddit.com", url)
            if "/comments/" in url:
                return _FakeResponse(comments_payload)
            return _FakeResponse(top_payload)

        config = {
            "type": "reddit",
            "name": "Reddit",
            "enabled": True,
            "source_type": "community",
            "sub_source_type": "reddit_top",
            "item_type": "discussion",
            "config": {
                "source": {"subreddit": "LocalLLaMA"},
                "fetch": {"max_retries": 1, "post_limit": 1, "max_comments_to_keep": 10},
                "filters": {"min_score": 1, "skip_nsfw": True, "skip_stickied": True, "skip_discussion_below": 0, "skip_self_text_below": 0},
                "enrich": [{"name": "top_comments", "when": "always"}],
                "runtime": {},
            },
        }

        with patch.dict("os.environ", {"REDDIT_CLIENT_ID": "client", "REDDIT_CLIENT_SECRET": "secret"}), patch(
            "scrapers.reddit.http_post",
            return_value=_FakeResponse(token_payload),
        ), patch("scrapers.reddit.http_get", side_effect=fake_get):
            result = run_config(config, "2026-06-20")

        row = result.rows[0]
        self.assertEqual(row["context_content"]["comments_fetch_via"], "oauth")
        self.assertEqual(row["context_content"]["top_comments"][0]["text"], "OAuth comment")


class OpenReviewEnrichmentTest(unittest.TestCase):
    def test_openreview_fetches_reply_details(self) -> None:
        note_payload = {
            "notes": [
                {
                    "id": "note1",
                    "forum": "forum1",
                    "content": {
                        "title": {"value": "OpenReview paper"},
                        "abstract": {"value": "Abstract"},
                        "authors": {"value": ["Alice"]},
                        "venueid": {"value": "Venue"},
                    },
                    "details": {"replyCount": 1},
                    "tcdate": 1781913600000,
                }
            ]
        }
        reply_payload = {
            "notes": [
                {"id": "note1"},
                {
                    "id": "reply1",
                    "replyto": "note1",
                    "invitations": ["Venue/-/Official_Review"],
                    "signatures": ["Reviewer_1"],
                    "content": {"review": {"value": "Detailed review"}, "rating": {"value": "8"}},
                    "tcdate": 1781917200000,
                },
            ]
        }

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            params = kwargs.get("params") or {}
            if isinstance(params, dict) and params.get("forum") == "forum1":
                return _FakeResponse(reply_payload)
            return _FakeResponse(note_payload)

        config = {
            "type": "openreview",
            "name": "OpenReview",
            "enabled": True,
            "source_type": "research",
            "sub_source_type": "openreview",
            "item_type": "paper",
            "config": {
                "source": {"venue_ids": ["Venue"], "invitations": []},
                "fetch": {"per_source": 1, "limit": 1, "reply_limit": 10, "sort_by": "reply_count"},
                "filters": {"min_reply_count": 0},
                "enrich": [{"name": "openreview_replies", "when": "always"}],
                "runtime": {},
            },
        }

        with patch("sources.openreview.http_get", side_effect=fake_get):
            result = run_config(config, "2026-06-20")

        replies = result.rows[0]["context_content"]["top_replies"]
        self.assertEqual(replies[0]["text"], "Detailed review")
        self.assertEqual(replies[0]["rating"], "8")


class GitHubReleasesEnrichmentTest(unittest.TestCase):
    def test_github_releases_preserves_download_counts_and_release_notes(self) -> None:
        payload = [
            {
                "id": 1,
                "html_url": "https://github.com/org/repo/releases/tag/v1",
                "name": "v1",
                "tag_name": "v1",
                "body": "Release notes",
                "published_at": "2026-06-20T00:00:00Z",
                "prerelease": False,
                "target_commitish": "main",
                "assets": [{"name": "wheel", "download_count": 42, "browser_download_url": "https://example.com/wheel"}],
                "author": {"login": "maintainer", "html_url": "https://github.com/maintainer"},
            }
        ]
        config = {
            "type": "github_releases",
            "name": "GitHub Releases",
            "enabled": True,
            "source_type": "code_host",
            "sub_source_type": "github_releases",
            "item_type": "release",
            "config": {
                "source": {"repositories": ["org/repo"]},
                "fetch": {"releases_per_repo": 1, "window_days": 7, "limit": 1, "sort_by": "asset_downloads"},
                "filters": {"skip_prerelease": True, "min_asset_downloads": 0},
                "enrich": [],
                "runtime": {},
            },
        }

        with patch("sources.github_releases.http_get", return_value=_FakeResponse(payload)):
            result = run_config(config, "2026-06-20")

        row = result.rows[0]
        self.assertEqual(row["metrics"]["asset_downloads"], 42)
        self.assertEqual(row["context_content"]["release_notes"], "Release notes")


class PyPIDownloadsEnrichmentTest(unittest.TestCase):
    def test_pypi_releases_fetch_recent_download_counts_and_release_notes(self) -> None:
        package_payload = {
            "info": {
                "summary": "Summary",
                "description": "Long description",
                "classifiers": ["Programming Language :: Python"],
                "author": "Alice",
            },
            "vulnerabilities": [],
            "releases": {
                "1.0.0": [
                    {
                        "upload_time_iso_8601": "2026-06-20T00:00:00.000Z",
                        "filename": "demo-1.0.0.tar.gz",
                        "packagetype": "sdist",
                        "size": 123,
                        "yanked": False,
                    }
                ]
            },
        }
        downloads_payload = {"data": {"last_day": 10, "last_week": 70, "last_month": 300}}

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            if "pypistats.org" in url:
                return _FakeResponse(downloads_payload)
            return _FakeResponse(package_payload)

        config = {
            "type": "pypi_package_releases",
            "name": "PyPI",
            "enabled": True,
            "source_type": "package_registry",
            "sub_source_type": "pypi_package_releases",
            "item_type": "package_release",
            "config": {
                "source": {"packages": ["demo"]},
                "fetch": {"window_days": 7, "limit": 1, "fetch_downloads": True},
                "filters": {"skip_prerelease": True, "skip_yanked": True},
                "enrich": [],
                "runtime": {},
            },
        }

        with patch("sources.package_releases.http_get", side_effect=fake_get):
            result = run_config(config, "2026-06-20")

        row = result.rows[0]
        self.assertEqual(row["metrics"]["downloads_last_week"], 70)
        self.assertTrue(row["metrics"]["downloads_available"])
        self.assertIn("Long description", row["context_content"]["release_notes"])


class LobstersEnrichmentTest(unittest.TestCase):
    def test_lobsters_fetches_top_comments(self) -> None:
        feed_payload = [
            {
                "short_id": "abc123",
                "created_at": "2026-06-19T10:00:00.000-05:00",
                "title": "Lobsters story",
                "url": "https://example.com/story",
                "score": 10,
                "flags": 0,
                "comment_count": 2,
                "description_plain": "",
                "submitter_user": "alice",
                "tags": ["programming"],
            }
        ]
        detail_payload = {
            "comments": [
                {"short_id": "low", "comment": "<p>Low</p>", "score": 1, "commenting_user": "low_user"},
                {"short_id": "high", "comment": "<p>High</p>", "score": 9, "commenting_user": "high_user"},
            ]
        }

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            if url.endswith("/hottest.json"):
                return _FakeResponse(feed_payload)
            return _FakeResponse(detail_payload)

        config = {
            "type": "lobsters",
            "name": "Lobsters",
            "enabled": True,
            "source_type": "NEWS",
            "sub_source_type": "lobsters_top",
            "item_type": "article",
            "config": {
                "source": {"feed": "hottest", "tags": []},
                "fetch": {"window_days": 7, "limit": 1, "comments_to_keep": 10},
                "filters": {"min_score": 0, "min_comments": 0, "tag_whitelist": [], "tag_blacklist": []},
                "enrich": [],
                "runtime": {},
            },
        }

        with patch("sources.lobsters.http_get", side_effect=fake_get):
            result = run_config(config, "2026-06-20")

        comments = result.rows[0]["context_content"]["top_comments"]
        self.assertEqual([comment["id"] for comment in comments], ["high", "low"])


if __name__ == "__main__":
    unittest.main()
