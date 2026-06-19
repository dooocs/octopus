from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infra.dao import OctopusDao, RawItemRecord


class JsonlSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def write(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str))
                f.write("\n")
        return len(rows)


class RdsSink:
    def write(self, rows: list[dict[str, Any]]) -> int:
        records = [RawItemRecord.from_mapping(row) for row in rows]
        if not records:
            return 0
        with OctopusDao.from_env() as dao:
            return dao.raw_items.upsert_many(records)
