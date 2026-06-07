from __future__ import annotations

import json
import os
from typing import Any, Iterable

import requests

CONFIG_TABLE = "octp_scraper_configs"
LOG_TABLE = "octp_scraper_logs"


def _clean_base_url(url: str) -> str:
    value = url.strip().rstrip("/")
    if not value:
        raise ValueError("SUPABASE_URL is required")
    return value


def _json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise TypeError(f"{field_name} must be a JSON object")


def runtime_config_from_row(row: dict[str, Any]) -> dict[str, Any]:
    scraper = row.get("scraper")
    name = row.get("name")
    source_type = row.get("source_type")
    sub_source_type = row.get("sub_source_type")
    item_type = row.get("item_type")

    required = {
        "scraper": scraper,
        "name": name,
        "source_type": source_type,
        "sub_source_type": sub_source_type,
        "item_type": item_type,
    }
    missing = [key for key, value in required.items() if value in (None, "")]
    if missing:
        raise ValueError(f"scraper config row is missing required fields: {', '.join(missing)}")

    return {
        "id": row.get("id"),
        "type": str(scraper),
        "name": str(name),
        "enabled": bool(row.get("enabled", True)),
        "priority": int(row.get("priority") or 100),
        "source_type": str(source_type),
        "sub_source_type": str(sub_source_type),
        "item_type": str(item_type),
        "config": _json_object(row.get("input"), field_name="input"),
    }


def split_supported_configs(
    configs: Iterable[dict[str, Any]],
    supported_types: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    supported = set(supported_types)
    runnable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for config in configs:
        if config["type"] in supported:
            runnable.append(config)
        else:
            skipped.append(config)

    return runnable, skipped


class SupabaseRestClient:
    def __init__(self, url: str, service_role_key: str) -> None:
        self.url = _clean_base_url(url)
        self.service_role_key = service_role_key.strip()
        if not self.service_role_key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is required")

    @classmethod
    def from_env(cls) -> "SupabaseRestClient":
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

    def fetch_enabled_config_rows(self) -> list[dict[str, Any]]:
        response = requests.get(
            self._table_url(CONFIG_TABLE),
            headers=self.headers,
            params={
                "select": "*",
                "enabled": "eq.true",
                "order": "priority.asc,name.asc",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise TypeError("Supabase config response must be a JSON array")
        return data

    def create_scraper_log(self, payload: dict[str, Any]) -> str | None:
        response = requests.post(
            self._table_url(LOG_TABLE),
            headers={**self.headers, "Prefer": "return=representation"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            log_id = data[0].get("id")
            return str(log_id) if log_id else None
        return None

    def update_scraper_log(self, log_id: str, payload: dict[str, Any]) -> None:
        response = requests.patch(
            self._table_url(LOG_TABLE),
            headers={**self.headers, "Prefer": "return=minimal"},
            params={"id": f"eq.{log_id}"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()


def load_enabled_runtime_configs(client: SupabaseRestClient) -> list[dict[str, Any]]:
    return [runtime_config_from_row(row) for row in client.fetch_enabled_config_rows()]
