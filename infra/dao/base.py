from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class RdsConfig:
    host: str
    user: str
    password: str
    database: str
    port: int = 3306
    charset: str = "utf8mb4"
    connect_timeout: int = 10

    @classmethod
    def from_env(cls, prefix: str = "OCTOPUS_RDS_") -> RdsConfig:
        def _required(name: str) -> str:
            value = os.getenv(f"{prefix}{name}", "")
            if not value:
                raise ValueError(f"missing required env: {prefix}{name}")
            return value

        return cls(
            host=_required("HOST"),
            port=int(os.getenv(f"{prefix}PORT", "3306")),
            user=_required("USER"),
            password=_required("PASSWORD"),
            database=_required("DATABASE"),
            charset=os.getenv(f"{prefix}CHARSET", "utf8mb4"),
            connect_timeout=int(os.getenv(f"{prefix}CONNECT_TIMEOUT", "10")),
        )


class AliyunRdsDaoBase:
    """Base class for Aliyun RDS MySQL write operations."""

    def __init__(self, config: RdsConfig):
        self.config = config
        self._conn: Any | None = None

    @classmethod
    def from_env(cls, prefix: str = "OCTOPUS_RDS_") -> AliyunRdsDaoBase:
        return cls(RdsConfig.from_env(prefix))

    def connect(self) -> Any:
        if self._conn is None:
            try:
                import pymysql
            except ImportError as exc:
                raise RuntimeError("PyMySQL is required for Aliyun RDS writes") from exc

            self._conn = pymysql.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                database=self.config.database,
                charset=self.config.charset,
                connect_timeout=self.config.connect_timeout,
                autocommit=False,
            )
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def execute_update(self, sql: str, params: Sequence[Any]) -> int:
        conn = self.connect()
        with conn.cursor() as cursor:
            affected = cursor.execute(sql, params)
        conn.commit()
        return affected

    def executemany_update(self, sql: str, params_seq: Iterable[Sequence[Any]]) -> int:
        params = list(params_seq)
        if not params:
            return 0

        conn = self.connect()
        with conn.cursor() as cursor:
            affected = cursor.executemany(sql, params)
        conn.commit()
        return affected

    def rollback(self) -> None:
        if self._conn is not None:
            self._conn.rollback()

    def __enter__(self) -> AliyunRdsDaoBase:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None:
            self.rollback()
        self.close()
