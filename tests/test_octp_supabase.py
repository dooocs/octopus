from __future__ import annotations

import unittest

from scripts.octp_supabase import runtime_config_from_row, split_supported_configs


class OctpSupabaseConfigTest(unittest.TestCase):
    def test_runtime_config_from_row_maps_new_admin_fields(self) -> None:
        row = {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "HackerNews",
            "scraper": "hackernews",
            "enabled": True,
            "priority": 30,
            "source_type": "NEWS",
            "sub_source_type": "hackernews",
            "item_type": "article",
            "input": {"new_n": 500, "min_score": 50},
        }

        config = runtime_config_from_row(row)

        self.assertEqual(config["id"], row["id"])
        self.assertEqual(config["type"], "hackernews")
        self.assertEqual(config["name"], "HackerNews")
        self.assertEqual(config["source_type"], "NEWS")
        self.assertEqual(config["sub_source_type"], "hackernews")
        self.assertEqual(config["item_type"], "article")
        self.assertEqual(config["config"], {"new_n": 500, "min_score": 50})

    def test_runtime_config_from_row_accepts_json_string_input(self) -> None:
        config = runtime_config_from_row(
            {
                "name": "RSS",
                "scraper": "rss",
                "source_type": "WEBSITE",
                "sub_source_type": "openai_blog",
                "item_type": "article",
                "input": '{"max_items": 5}',
            }
        )

        self.assertEqual(config["config"], {"max_items": 5})

    def test_split_supported_configs_keeps_unsupported_separate(self) -> None:
        configs = [
            {"name": "GitHub Trending", "type": "github_trending"},
            {"name": "Legacy Twitter", "type": "twitter_nitter"},
        ]

        runnable, skipped = split_supported_configs(configs, {"github_trending"})

        self.assertEqual([c["name"] for c in runnable], ["GitHub Trending"])
        self.assertEqual([c["name"] for c in skipped], ["Legacy Twitter"])


if __name__ == "__main__":
    unittest.main()
