from __future__ import annotations

from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - exercised when local env lacks jsonschema
    Draft202012Validator = None  # type: ignore[assignment]

from .contracts import ChannelSpec, InputConfig, RawItem, ScraperConfig


BASE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["source", "fetch", "filters", "enrich"],
    "additionalProperties": False,
    "properties": {
        "source": {"type": "object"},
        "fetch": {"type": "object"},
        "filters": {"type": "object"},
        "enrich": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "when": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
    },
}


def validate_channel_spec(spec: ChannelSpec) -> None:
    if Draft202012Validator is not None:
        Draft202012Validator.check_schema(spec.input_schema)
    InputConfig.from_mapping(spec.default_input)
    validate_input(spec, spec.default_input)


def validate_input(spec: ChannelSpec, input_value: dict[str, Any]) -> None:
    InputConfig.from_mapping(input_value)
    _validate_schema(BASE_INPUT_SCHEMA, input_value)
    _validate_schema(spec.input_schema, input_value)

    supported = set(spec.supported_enrichers)
    if supported:
        for item in input_value.get("enrich", []):
            name = item.get("name") if isinstance(item, dict) else None
            if name and name not in supported:
                raise ValueError(f"unsupported enrich step for {spec.scraper}: {name}")


def validate_config(spec: ChannelSpec, config: ScraperConfig) -> None:
    if config.input_schema_version != spec.input_schema_version:
        raise ValueError(
            f"{config.name} input_schema_version={config.input_schema_version} "
            f"does not match {spec.scraper} version={spec.input_schema_version}"
        )
    validate_input(spec, config.input.to_dict())


def validate_raw_item(item: RawItem) -> None:
    row = item.to_output_dict()
    required = ["id", "url", "source_type", "sub_source_type", "item_type", "snapshot_date", "title"]
    missing = [key for key in required if row.get(key) in (None, "")]
    if missing:
        raise ValueError(f"raw item is missing required fields: {', '.join(missing)}")
    for key in ("metrics", "context_content", "extra", "scrape_config_snapshot"):
        value = row.get(key)
        if value is not None and not isinstance(value, dict):
            raise TypeError(f"raw item {key} must be a JSON object or None")


def _validate_schema(schema: dict[str, Any], value: Any) -> None:
    if Draft202012Validator is not None:
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
        if errors:
            raise ValueError("; ".join(error.message for error in errors))
        return
    _validate_minimal_schema(schema, value, path="")


def _validate_minimal_schema(schema: dict[str, Any], value: Any, *, path: str) -> None:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            raise TypeError(f"{path or 'value'} must be an object")
        required = schema.get("required") or []
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"{path or 'value'} missing required keys: {', '.join(missing)}")
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value.keys()) - set(properties.keys()))
            if extra:
                raise ValueError(f"{path or 'value'} has unknown keys: {', '.join(extra)}")
        for key, child_schema in properties.items():
            if key in value:
                child_path = f"{path}.{key}" if path else key
                _validate_minimal_schema(child_schema, value[key], path=child_path)
    elif expected_type == "array":
        if not isinstance(value, list):
            raise TypeError(f"{path or 'value'} must be an array")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_minimal_schema(item_schema, item, path=f"{path}[{index}]")
    elif expected_type == "string":
        if not isinstance(value, str):
            raise TypeError(f"{path or 'value'} must be a string")
        if schema.get("minLength") and len(value) < int(schema["minLength"]):
            raise ValueError(f"{path or 'value'} is too short")
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{path or 'value'} must be an integer")
    elif expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"{path or 'value'} must be a number")
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            raise TypeError(f"{path or 'value'} must be a boolean")

    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise ValueError(f"{path or 'value'} must be one of: {', '.join(map(str, enum))}")
