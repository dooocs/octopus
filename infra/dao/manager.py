from __future__ import annotations

from .base import AliyunRdsDaoBase, RdsConfig
from .models import RawItemRecord
from .raw_items import RawItemsDao


class OctopusDao:
    """Single entry point for DAO calls."""

    def __init__(self, db: AliyunRdsDaoBase):
        self.db = db
        self.raw_items = RawItemsDao(db)

    @classmethod
    def from_config(cls, config: RdsConfig) -> OctopusDao:
        return cls(AliyunRdsDaoBase(config))

    @classmethod
    def from_env(cls, prefix: str = "OCTOPUS_RDS_") -> OctopusDao:
        return cls(AliyunRdsDaoBase.from_env(prefix))

    def upsert_raw_item(self, record: RawItemRecord) -> int:
        return self.raw_items.upsert(record)

    def upsert_raw_items(self, records: list[RawItemRecord]) -> int:
        return self.raw_items.upsert_many(records)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> OctopusDao:
        self.db.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.db.__exit__(exc_type, exc, traceback)
