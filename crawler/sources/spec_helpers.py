from __future__ import annotations

from typing import Any


def input_schema(
    *,
    source: dict[str, Any] | None = None,
    fetch: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    enrich_names: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["source", "fetch", "filters", "enrich"],
        "additionalProperties": False,
        "properties": {
            "source": {"type": "object", "additionalProperties": False, "properties": source or {}},
            "fetch": {"type": "object", "additionalProperties": False, "properties": fetch or {}},
            "filters": {"type": "object", "additionalProperties": False, "properties": filters or {}},
            "enrich": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "enum": enrich_names or []},
                        "when": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
        },
    }


def default_input(
    *,
    source: dict[str, Any] | None = None,
    fetch: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    enrich: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source": source or {},
        "fetch": fetch or {},
        "filters": filters or {},
        "enrich": enrich or [],
    }


STRING = {"type": "string"}
NUMBER = {"type": "number"}
INTEGER = {"type": "integer"}
BOOLEAN = {"type": "boolean"}
STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
JSON_OBJECT = {"type": "object", "additionalProperties": True}
