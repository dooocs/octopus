from __future__ import annotations

import unittest
from unittest.mock import patch

from core.registry import export_specs
from core.runner import run_config


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class NewSourceSpecTest(unittest.TestCase):
    def test_exported_specs_include_new_quant_sources(self) -> None:
        scrapers = {item["scraper"] for item in export_specs()}

        self.assertIn("arxiv", scrapers)
        self.assertIn("github_releases", scrapers)
        self.assertIn("npm_package_releases", scrapers)
        self.assertIn("pypi_package_releases", scrapers)
        self.assertIn("openreview", scrapers)
        self.assertIn("lobsters", scrapers)


class LobstersAdapterTest(unittest.TestCase):
    def test_lobsters_keeps_native_metrics_and_sorts_by_score(self) -> None:
        payload = [
            {
                "short_id": "low",
                "created_at": "2026-06-19T10:00:00.000-05:00",
                "title": "Low",
                "url": "https://example.com/low",
                "score": 3,
                "flags": 0,
                "comment_count": 9,
                "description_plain": "",
                "submitter_user": "a",
                "tags": ["programming"],
                "short_id_url": "https://lobste.rs/s/low",
                "comments_url": "https://lobste.rs/s/low/low",
            },
            {
                "short_id": "high",
                "created_at": "2026-06-19T10:00:00.000-05:00",
                "title": "High",
                "url": "https://example.com/high",
                "score": 10,
                "flags": 0,
                "comment_count": 1,
                "description_plain": "",
                "submitter_user": "b",
                "tags": ["programming"],
                "short_id_url": "https://lobste.rs/s/high",
                "comments_url": "https://lobste.rs/s/high/high",
            },
        ]
        config = {
            "type": "lobsters",
            "name": "Lobsters",
            "enabled": True,
            "source_type": "community",
            "sub_source_type": "lobsters",
            "item_type": "article",
            "config": {
                "source": {"feed": "hottest", "tags": []},
                "fetch": {"window_days": 7, "limit": 1},
                "filters": {"min_score": 0, "min_comments": 0, "tag_whitelist": [], "tag_blacklist": []},
                "enrich": [],
                "runtime": {},
            },
        }

        with patch("sources.lobsters.http_get", return_value=_FakeResponse(payload)):
            result = run_config(config, "2026-06-20")

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["title"], "High")
        self.assertEqual(result.rows[0]["metrics"], {"score": 10, "comment_count": 1, "flags": 0})


class NpmPackageReleasesAdapterTest(unittest.TestCase):
    def test_npm_search_preserves_download_metrics(self) -> None:
        payload = {
            "objects": [
                {
                    "downloads": {"weekly": 12345, "monthly": 45678},
                    "dependents": "99",
                    "updated": "2026-06-19T09:00:00.000Z",
                    "searchScore": 12.3,
                    "package": {
                        "name": "demo-ai",
                        "version": "1.2.3",
                        "description": "Demo package",
                        "date": "2026-06-19T09:00:00.000Z",
                        "keywords": ["ai"],
                        "links": {"npm": "https://www.npmjs.com/package/demo-ai"},
                        "publisher": {"username": "publisher"},
                    },
                    "score": {
                        "final": 1.0,
                        "detail": {"popularity": 0.9, "quality": 0.8, "maintenance": 0.7},
                    },
                }
            ]
        }
        config = {
            "type": "npm_package_releases",
            "name": "npm",
            "enabled": True,
            "source_type": "package_registry",
            "sub_source_type": "npm_package_releases",
            "item_type": "package_release",
            "config": {
                "source": {"search_queries": [{"q": "keywords:ai", "label": "ai"}], "packages": []},
                "fetch": {"search_size": 1, "window_days": 7, "limit": 1},
                "filters": {"min_weekly_downloads": 1000, "skip_prerelease": True},
                "enrich": [],
                "runtime": {},
            },
        }

        with patch("sources.package_releases.http_get", return_value=_FakeResponse(payload)):
            result = run_config(config, "2026-06-20")

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["metrics"]["downloads_weekly"], 12345)
        self.assertEqual(result.rows[0]["metrics"]["dependents"], 99)
        self.assertEqual(result.rows[0]["extra"]["ecosystem"], "npm")


if __name__ == "__main__":
    unittest.main()
