from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from typing import Any

import requests
from dotenv import load_dotenv

from infra.dao.base import RdsConfig
from infra.dao.raw_items import RawItemsDao

SNAPSHOT_TABLE = "octp_snapshot_raw_items"
JSON_COLUMNS = {
    "metrics",
    "context_content",
    "extra",
    "scrape_config_snapshot",
}


def _clean_base_url(url: str) -> str:
    value = url.strip().rstrip("/")
    if not value:
        raise ValueError("SUPABASE_URL is required")
    return value


def _json_value(value: Any, *, field_name: str) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        if value == "":
            return None
        parsed = json.loads(value)
        if isinstance(parsed, (dict, list)):
            return parsed
    raise TypeError(f"{field_name} must be JSON object, JSON array, string, or None")


def _json_ready(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def normalize_raw_item_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for column in RawItemsDao.COLUMNS:
        value = row.get(column)
        if column in JSON_COLUMNS:
            normalized[column] = _json_value(value, field_name=column)
        else:
            normalized[column] = _json_ready(value)
    return normalized


def iter_rds_raw_items(snapshot_date: str, *, batch_size: int) -> Iterator[dict[str, Any]]:
    try:
        import pymysql
        import pymysql.cursors
    except ImportError as exc:
        raise RuntimeError("PyMySQL is required for Aliyun RDS snapshot sync") from exc

    config = RdsConfig.from_env()
    ssl_options = {"ca": config.ssl_ca} if config.ssl_ca else None
    conn = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset=config.charset,
        connect_timeout=config.connect_timeout,
        ssl=ssl_options,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )

    columns = ", ".join(f"`{column}`" for column in RawItemsDao.COLUMNS)
    sql = (
        f"select {columns} from `raw_items` "
        "where `snapshot_date` = %s "
        "order by `source_type`, `sub_source_type`, `updated_date` desc, `id`"
    )

    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (snapshot_date,))
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    yield normalize_raw_item_row(row)
    finally:
        conn.close()


class SupabaseSnapshotClient:
    def __init__(self, url: str, service_role_key: str) -> None:
        self.url = _clean_base_url(url)
        self.service_role_key = service_role_key.strip()
        if not self.service_role_key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is required")

    @classmethod
    def from_env(cls) -> "SupabaseSnapshotClient":
        return cls(
            os.getenv("SUPABASE_URL", ""),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }

    def _table_url(self, table_name: str) -> str:
        return f"{self.url}/rest/v1/{table_name}"

    def replace_rows(self, rows: Iterable[dict[str, Any]], *, chunk_size: int) -> int:
        delete_response = requests.delete(
            self._table_url(SNAPSHOT_TABLE),
            headers={**self.headers, "Prefer": "return=minimal"},
            params={"id": "not.is.null"},
            timeout=60,
        )
        delete_response.raise_for_status()

        inserted = 0
        chunk: list[dict[str, Any]] = []
        for row in rows:
            chunk.append(row)
            if len(chunk) >= chunk_size:
                self._insert_chunk(chunk)
                inserted += len(chunk)
                chunk = []

        if chunk:
            self._insert_chunk(chunk)
            inserted += len(chunk)

        return inserted

    def _insert_chunk(self, rows: list[dict[str, Any]]) -> None:
        response = requests.post(
            self._table_url(SNAPSHOT_TABLE),
            headers={**self.headers, "Prefer": "return=minimal"},
            json=rows,
            timeout=60,
        )
        response.raise_for_status()


def sync_raw_items_snapshot(
    *,
    snapshot_date: str,
    batch_size: int,
    chunk_size: int,
    client: SupabaseSnapshotClient | None = None,
) -> int:
    snapshot_client = client or SupabaseSnapshotClient.from_env()
    rows = iter_rds_raw_items(snapshot_date, batch_size=batch_size)
    return snapshot_client.replace_rows(rows, chunk_size=chunk_size)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh the Supabase raw_items snapshot table")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Snapshot date to copy from Aliyun RDS raw_items, e.g. 2026-06-13.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows fetched per Aliyun RDS batch.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=300,
        help="Rows inserted per Supabase REST request.",
    )
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    inserted = sync_raw_items_snapshot(
        snapshot_date=args.date,
        batch_size=args.batch_size,
        chunk_size=args.chunk_size,
    )
    print(
        f"raw_items snapshot refresh complete: "
        f"table={SNAPSHOT_TABLE} snapshot_date={args.date} rows={inserted}"
    )


if __name__ == "__main__":
    main()
