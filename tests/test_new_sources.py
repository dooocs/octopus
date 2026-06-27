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
            "scraper": "lobsters",
            "name": "Lobsters",
            "enabled": True,
            "source_type": "community",
            "sub_source_type": "lobsters",
            "item_type": "article",
            "input": {
                "source": {"feed": "hottest", "tags": []},
                "fetch": {"window_days": 3650, "limit": 1},
                "filters": {"min_score": 0, "min_comments": 0, "tag_whitelist": [], "tag_blacklist": []},
                "enrich": [],
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
            "scraper": "npm_package_releases",
            "name": "npm",
            "enabled": True,
            "source_type": "package_registry",
            "sub_source_type": "npm_package_releases",
            "item_type": "package_release",
            "input": {
                "source": {"search_queries": [{"q": "keywords:ai", "label": "ai"}], "packages": []},
                "fetch": {"search_size": 1, "window_days": 3650, "limit": 1},
                "filters": {"min_weekly_downloads": 1000, "skip_prerelease": True},
                "enrich": [],
            },
        }

        with patch("sources.package_releases.http_get", return_value=_FakeResponse(payload)):
            result = run_config(config, "2026-06-20")

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["metrics"]["downloads_weekly"], 12345)
        self.assertEqual(result.rows[0]["metrics"]["dependents"], 99)
        self.assertEqual(result.rows[0]["extra"]["ecosystem"], "npm")
        self.assertEqual(result.rows[0]["context_content"]["release_notes"], "Demo package")

    def test_npm_watched_package_fetches_downloads_and_release_notes(self) -> None:
        registry_payload = {
            "dist-tags": {"latest": "1.2.3"},
            "time": {"1.2.3": "2026-06-19T09:00:00.000Z"},
            "versions": {
                "1.2.3": {
                    "description": "Release description",
                    "readme": "Detailed release notes",
                    "author": "Alice",
                }
            },
        }

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            if "last-week" in url:
                return _FakeResponse({"downloads": 1000})
            if "last-month" in url:
                return _FakeResponse({"downloads": 4000})
            return _FakeResponse(registry_payload)

        config = {
            "scraper": "npm_package_releases",
            "name": "npm",
            "enabled": True,
            "source_type": "package_registry",
            "sub_source_type": "npm_package_releases",
            "item_type": "package_release",
            "input": {
                "source": {"search_queries": [], "packages": ["demo-ai"]},
                "fetch": {"search_size": 1, "window_days": 3650, "limit": 1},
                "filters": {"min_weekly_downloads": 100000, "skip_prerelease": True},
                "enrich": [],
            },
        }

        with patch("sources.package_releases.http_get", side_effect=fake_get):
            result = run_config(config, "2026-06-20")

        row = result.rows[0]
        self.assertEqual(row["metrics"]["downloads_weekly"], 1000)
        self.assertEqual(row["metrics"]["downloads_monthly"], 4000)
        self.assertIn("Detailed release notes", row["context_content"]["release_notes"])
        self.assertEqual(row["author_id"], "Alice")


class PyPIPackageReleasesPruneTest(unittest.TestCase):
    def test_pypi_prune_uses_discovered_release_date_before_download_enrichment(self) -> None:
        payloads = {
            "oldpopular": {
                "info": {"summary": "Old popular", "description": "Old body", "author": "Bob"},
                "vulnerabilities": [],
                "releases": {
                    "1.0.0": [
                        {
                            "upload_time_iso_8601": "2026-06-18T00:00:00.000Z",
                            "filename": "oldpopular-1.0.0.tar.gz",
                            "packagetype": "sdist",
                            "size": 123,
                            "yanked": False,
                        }
                    ]
                },
            },
            "newquiet": {
                "info": {"summary": "New quiet", "description": "New body", "author": "Alice"},
                "vulnerabilities": [],
                "releases": {
                    "2.0.0": [
                        {
                            "upload_time_iso_8601": "2026-06-20T00:00:00.000Z",
                            "filename": "newquiet-2.0.0.tar.gz",
                            "packagetype": "sdist",
                            "size": 456,
                            "yanked": False,
                        }
                    ]
                },
            },
        }
        called_urls: list[str] = []

        def fake_get(url: str, **kwargs: object) -> _FakeResponse:
            called_urls.append(url)
            if "pypistats.org" in url:
                downloads = 999999 if "oldpopular" in url else 1
                return _FakeResponse({"data": {"last_day": downloads, "last_week": downloads, "last_month": downloads}})
            if "oldpopular" in url:
                return _FakeResponse(payloads["oldpopular"])
            return _FakeResponse(payloads["newquiet"])

        config = {
            "scraper": "pypi_package_releases",
            "name": "PyPI",
            "enabled": True,
            "source_type": "package_registry",
            "sub_source_type": "pypi_package_releases",
            "item_type": "package_release",
            "input": {
                "source": {"packages": ["oldpopular", "newquiet"]},
                "fetch": {"window_days": 3650, "limit": 1, "fetch_downloads": True},
                "filters": {"skip_prerelease": True, "skip_yanked": True},
                "enrich": [],
            },
        }

        with patch("sources.package_releases.http_get", side_effect=fake_get):
            result = run_config(config, "2026-06-20")

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["title"], "newquiet 2.0.0")
        self.assertEqual(result.rows[0]["metrics"]["downloads_last_week"], 1)
        self.assertFalse(any("oldpopular/recent" in url for url in called_urls))


if __name__ == "__main__":
    unittest.main()
