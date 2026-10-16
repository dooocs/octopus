"""Run one processing pipeline: python -m scripts.run_pipeline feed_digest"""

from __future__ import annotations

import argparse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:
        return None

from infra.dao import OctopusDao
from infra.gateways.llm import ChatClient
from infra.supabase import SupabaseRestClient, SupabaseTable
from processing.core import Deps, run_pipeline


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run one Octopus processing pipeline")
    parser.add_argument("slug", help="octp_pipelines.slug, e.g. feed_digest")
    args = parser.parse_args()

    supabase = SupabaseRestClient.from_env()
    with OctopusDao.from_env() as dao:
        deps = Deps(
            raw_items=dao.raw_items,
            table=lambda name: SupabaseTable(supabase, name),
            llm=ChatClient.from_config,
        )
        run_pipeline(args.slug, deps)


if __name__ == "__main__":
    main()
