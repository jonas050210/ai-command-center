"""Async SQLite access (aiosqlite).

One shared connection guarded by an asyncio.Lock — SQLite serializes
writes anyway and this keeps the design simple, fast and fully async
from FastAPI's perspective. WAL mode allows concurrent readers.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

log = logging.getLogger("aicc.db")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.commit()
        log.info("database connected: %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Cursor:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            await self.conn.commit()
            return cur

    async def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> None:
        async with self._lock:
            await self.conn.executemany(sql, seq)
            await self.conn.commit()

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            row = await cur.fetchone()
            return row[0] if row else None

    async def health(self) -> bool:
        try:
            await self.scalar("SELECT 1")
            return True
        except Exception:  # pragma: no cover
            log.exception("db health check failed")
            return False
