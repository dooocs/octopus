from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from infra.dao import OctopusDao, RawItemRecord


def _smoke_id() -> str:
    identity = "octopus:aliyun_rds_test:raw_items"
    return hashlib.md5(identity.encode()).hexdigest()


def _run_url() -> str:
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY", "walihome/octopus")
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    return f"{server_url}/{repository}/actions/runs/{run_id}"


def build_record() -> RawItemRecord:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")

    return RawItemRecord(
        id=_smoke_id(),
        url=_run_url(),
        source_type="system",
        sub_source_type="aliyun_rds_test",
        item_type="smoke_test",
        author_id="github_actions",
        author_url="https://github.com/features/actions",
        created_date=now,
        updated_date=now,
        source_published_date=None,
        snapshot_date=now.date(),
        title="Octopus Aliyun RDS smoke test",
        metrics={
            "github_run_id": run_id,
            "github_run_attempt": run_attempt,
        },
        content="This row was written by the manually triggered aliyun_rds_test workflow.",
        context_content={
            "workflow": "aliyun_rds_test",
            "purpose": "Validate Octopus DAO connectivity and raw_items upsert against Aliyun RDS.",
        },
        extra={
            "repository": os.getenv("GITHUB_REPOSITORY", "local"),
            "ref": os.getenv("GITHUB_REF", ""),
            "sha": os.getenv("GITHUB_SHA", ""),
            "actor": os.getenv("GITHUB_ACTOR", ""),
        },
        scrape_config_snapshot={
            "script": "scripts/aliyun_rds_test_insert.py",
            "mode": "manual_smoke_test",
        },
    )


def main() -> None:
    load_dotenv()
    record = build_record()
    with OctopusDao.from_env() as dao:
        affected = dao.upsert_raw_item(record)

    print(f"raw_items smoke upsert complete: id={record.id}, affected={affected}")


if __name__ == "__main__":
    main()
