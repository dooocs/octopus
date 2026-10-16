"""Processing layer: pipelines that read raw_items and write their own outputs.

Design: docs/feed-pipeline-design.md §5. A pipeline kind is code (its steps and
output shape); an octp_pipelines row is configuration (which data it reads,
which model, prompt version, enabled). This package never imports `crawler`:
the only contract between the layers is raw_items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, Sequence

PIPELINES_TABLE = "octp_pipelines"
PIPELINE_RUNS_TABLE = "octp_pipeline_runs"


class RawItemSource(Protocol):
    def select_published_since(self, sub_source_types: Sequence[str], since: datetime) -> list[dict[str, Any]]: ...

    def select_by_ids(self, ids: Sequence[str]) -> list[dict[str, Any]]: ...


class Table(Protocol):
    def select(self, params: dict[str, str]) -> list[dict[str, Any]]: ...

    def upsert(self, rows: list[dict[str, Any]], on_conflict: str) -> None: ...

    def insert(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update(self, filters: dict[str, str], payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class PipelineConfig:
    id: str | None
    slug: str
    name: str
    kind: str
    enabled: bool
    input: dict[str, Any]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PipelineConfig":
        for key in ("slug", "name", "kind"):
            if not row.get(key):
                raise ValueError(f"pipeline row is missing {key}")
        value = row.get("input") or {}
        if not isinstance(value, dict):
            raise TypeError("pipeline input must be a JSON object")
        return cls(
            id=str(row["id"]) if row.get("id") else None,
            slug=str(row["slug"]),
            name=str(row["name"]),
            kind=str(row["kind"]),
            enabled=bool(row.get("enabled", True)),
            input=dict(value),
        )


@dataclass
class Deps:
    raw_items: RawItemSource
    table: Callable[[str], Table]
    llm: Callable[[dict[str, Any]], Any]  # llm config -> client with .complete() and .usage
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))


PipelineFn = Callable[[PipelineConfig, Deps], dict[str, Any]]
_KINDS: dict[str, PipelineFn] = {}


def register_pipeline(kind: str) -> Callable[[PipelineFn], PipelineFn]:
    def decorate(fn: PipelineFn) -> PipelineFn:
        _KINDS[kind] = fn
        return fn

    return decorate


def pipeline_kinds() -> list[str]:
    _load_all()
    return sorted(_KINDS)


def _load_all() -> None:
    import processing.feed_digest  # noqa: F401


def run_pipeline(slug: str, deps: Deps) -> dict[str, Any]:
    """Runs one configured pipeline and records the run. Raises on failure."""
    _load_all()
    rows = deps.table(PIPELINES_TABLE).select({"slug": f"eq.{slug}", "limit": "1"})
    if not rows:
        raise ValueError(f"no pipeline configured with slug {slug!r}")
    config = PipelineConfig.from_row(rows[0])
    if not config.enabled:
        print(f"pipeline {slug} is disabled; nothing to do")
        return {"status": "skipped"}
    fn = _KINDS.get(config.kind)
    if fn is None:
        raise ValueError(f"unknown pipeline kind {config.kind!r}; available={sorted(_KINDS)}")

    runs = deps.table(PIPELINE_RUNS_TABLE)
    run = runs.insert({"pipeline_id": config.id, "status": "running"})
    run_filter = {"id": f"eq.{run['id']}"} if run.get("id") else None
    try:
        summary = fn(config, deps)
    except Exception as exc:
        if run_filter:
            runs.update(run_filter, {
                "status": "failed",
                "finished_at": deps.now().isoformat(),
                "error_message": f"{exc.__class__.__name__}: {exc}"[:2000],
            })
        raise
    if run_filter:
        runs.update(run_filter, {"status": "success", "finished_at": deps.now().isoformat(), "summary": summary})
    print(f"pipeline {slug} done: {summary}")
    return {"status": "success", **summary}
