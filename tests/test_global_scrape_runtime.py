from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch

from crawler.core.contracts import ScrapeTaskResult
from crawler.runtime import global_scrape


def _row(sub: str, enabled: bool, archived: bool = False) -> dict:
    return {
        "id": f"id-{sub}", "name": sub, "scraper": "twitter_feed", "enabled": enabled, "priority": 100,
        "source_type": "social", "sub_source_type": sub, "item_type": "post", "input_schema_version": 1,
        "input": {"source": {}, "fetch": {}, "filters": {}, "enrich": []},
        "archived_at": "2026-10-16T00:00:00Z" if archived else None,
    }


class FakeClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.states: list[dict] = []

    def fetch_enabled_config_rows(self):
        return [r for r in self.rows if r["enabled"]]

    def fetch_config_rows(self):
        return list(self.rows)

    def create_scrape_run(self, payload):
        return "run-1"

    def update_scrape_run(self, run_id, payload):
        pass

    def create_scrape_task(self, payload):
        return "task-1"

    def update_scrape_task(self, task_id, payload):
        pass

    def fetch_scraper_state(self, config_id):
        return {}

    def upsert_scraper_state(self, payload):
        self.states.append(payload)


def _args(**overrides) -> argparse.Namespace:
    args = global_scrape.build_parser().parse_args([])
    args.output = ""
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


class RuntimeTest(unittest.TestCase):
    def _run(self, client: FakeClient, **overrides):
        ran: list[str] = []

        def fake_run_config(config, date, **kwargs):
            ran.append(config["sub_source_type"])
            return ScrapeTaskResult(rows=[], items_discovered=0, items_filtered=0, items_enriched=0, state={"feed_cursor": "c"})

        class FakeRds:
            def write(self, rows):
                return len(rows)

        with patch.object(global_scrape.SupabaseRestClient, "from_env", return_value=client), \
                patch.object(global_scrape, "run_config", side_effect=fake_run_config), \
                patch.object(global_scrape, "RdsOutput", FakeRds):
            code = global_scrape.run_global_scrape(_args(**overrides))
        return code, ran

    def test_include_disabled_runs_the_named_disabled_config_but_never_archived_ones(self) -> None:
        client = FakeClient([_row("x_feed_accounts", False), _row("old", False, archived=True), _row("other", True)])
        code, ran = self._run(client, only="x_feed_accounts,old", include_disabled=True)
        self.assertEqual((code, ran), (0, ["x_feed_accounts"]))

    def test_disabled_configs_are_not_run_without_the_switch(self) -> None:
        client = FakeClient([_row("x_feed_accounts", False)])
        code, ran = self._run(client, only="x_feed_accounts")
        self.assertEqual((code, ran), (0, []))

    def test_include_disabled_requires_only(self) -> None:
        with self.assertRaisesRegex(ValueError, "needs --only"):
            self._run(FakeClient([]), include_disabled=True)

    def test_state_only_advances_when_rows_reach_rds(self) -> None:
        client = FakeClient([_row("x_feed_accounts", True)])
        self._run(client, only="x_feed_accounts")
        self.assertEqual(client.states, [], "a JSONL-only run must not move the cursor")
        self._run(client, only="x_feed_accounts", write_rds=True)
        self.assertEqual(client.states[0]["state"], {"feed_cursor": "c"})


if __name__ == "__main__":
    unittest.main()
