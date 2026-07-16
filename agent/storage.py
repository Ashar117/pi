"""T-165: StorageBackend seam — thin transport abstraction for MemoryTools.

Purpose: decouple tier logic (what to store, when, how to prioritise) from
transport (sqlite3.connect, supabase calls). The seam makes the memory layer
testable without a real database and makes provider swaps mechanical.

Design contract
---------------
StorageBackend exposes only ONE primitive: connect() → sqlite3.Connection.
Callers commit / close as they already do; no ORM, no change to SQL.

Current implementors:
  SQLiteStorageBackend   — production; wraps sqlite3.connect(path)
  InMemoryStorageBackend — tests; shared :memory: DB, no tmp_path required

Migration approach (incremental, per T-165 migration_plan):
  Phase 1 (this commit): new T-186/T-205 conversation methods use the backend.
  Phase 2 (later): L3 read/write core methods migrated one by one.
  Phase 3 (later): Supabase transport extracted behind SupabaseBackend.
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import Optional


class StorageBackend(ABC):
    """Minimal transport seam — returns an open sqlite3.Connection."""

    @abstractmethod
    def connect(self) -> sqlite3.Connection:
        """Return an open sqlite3.Connection. Caller must .close() it."""


class SQLiteStorageBackend(StorageBackend):
    """Production backend: opens sqlite_path each time (matches existing pattern)."""

    def __init__(self, sqlite_path: str) -> None:
        self.sqlite_path = sqlite_path

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.sqlite_path)


class InMemoryStorageBackend(StorageBackend):
    """Test backend: one persistent :memory: connection shared across all calls.

    sqlite3 in-memory DBs are connection-scoped — each new connect() would
    give a fresh empty DB.  Holding one connection open and reusing it gives
    tests a stable schema that survives across method calls.

    Thread-safety note: not thread-safe.  Use only in single-threaded tests.
    """

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)

    def connect(self) -> sqlite3.Connection:
        return _NonClosingConnection(self._conn)

    def close(self) -> None:
        self._conn.close()


class _NonClosingConnection:
    """Proxy that makes close() a no-op so callers don't destroy the shared connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # Forward everything to the real connection
    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        pass  # keep the shared in-memory DB alive
