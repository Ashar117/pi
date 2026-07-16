"""Tests for T-165: StorageBackend seam (agent/storage.py + MemoryTools wiring)."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── StorageBackend types ──────────────────────────────────────────────────────

def test_sqlite_backend_connect_returns_connection(tmp_path):
    from agent.storage import SQLiteStorageBackend
    db = str(tmp_path / "test.db")
    backend = SQLiteStorageBackend(db)
    conn = backend.connect()
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_in_memory_backend_connect_returns_connection():
    from agent.storage import InMemoryStorageBackend
    backend = InMemoryStorageBackend()
    conn = backend.connect()
    assert hasattr(conn, "execute")
    conn.close()  # no-op


def test_in_memory_backend_persists_across_connect_calls():
    """Second connect() sees data written by the first."""
    from agent.storage import InMemoryStorageBackend
    backend = InMemoryStorageBackend()

    c1 = backend.connect()
    c1.execute("CREATE TABLE t (x TEXT)")
    c1.execute("INSERT INTO t VALUES ('hello')")
    c1.commit()
    c1.close()

    c2 = backend.connect()
    row = c2.execute("SELECT x FROM t").fetchone()
    c2.close()
    assert row[0] == "hello"


def test_in_memory_backend_close_does_not_crash():
    from agent.storage import InMemoryStorageBackend
    backend = InMemoryStorageBackend()
    conn = backend.connect()
    conn.close()  # should not raise
    backend.close()  # real close


def test_sqlite_backend_is_storage_backend_subclass():
    from agent.storage import StorageBackend, SQLiteStorageBackend
    assert issubclass(SQLiteStorageBackend, StorageBackend)


def test_in_memory_backend_is_storage_backend_subclass():
    from agent.storage import StorageBackend, InMemoryStorageBackend
    assert issubclass(InMemoryStorageBackend, StorageBackend)


# ── MemoryTools wiring ────────────────────────────────────────────────────────

def test_memory_tools_has_sqlite_backend(tmp_path):
    """MemoryTools.__init__ sets _sqlite_backend."""
    from tools.tools_memory import MemoryTools
    mem = MemoryTools.__new__(MemoryTools)
    mem.sqlite_path = str(tmp_path / "pi.db")
    mem.supabase = MagicMock()
    mem._init_sqlite()
    assert hasattr(mem, "_sqlite_backend")


def test_memory_tools_backend_is_sqlite_type(tmp_path):
    from tools.tools_memory import MemoryTools
    from agent.storage import SQLiteStorageBackend
    mem = MemoryTools.__new__(MemoryTools)
    mem.sqlite_path = str(tmp_path / "pi.db")
    mem.supabase = MagicMock()
    mem._init_sqlite()
    assert isinstance(mem._sqlite_backend, SQLiteStorageBackend)


def test_memory_tools_backend_injected_once(tmp_path):
    """_init_sqlite called twice does not replace a pre-existing backend."""
    from tools.tools_memory import MemoryTools
    from agent.storage import InMemoryStorageBackend
    mem = MemoryTools.__new__(MemoryTools)
    mem.sqlite_path = str(tmp_path / "pi.db")
    mem.supabase = MagicMock()
    mem._sqlite_backend = InMemoryStorageBackend()  # pre-inject
    mem._init_sqlite()
    assert isinstance(mem._sqlite_backend, InMemoryStorageBackend)  # not replaced


def test_conversation_methods_use_backend(tmp_path):
    """create_conversation + list_conversations round-trip through the backend."""
    from tools.tools_memory import MemoryTools
    from agent.storage import InMemoryStorageBackend

    mem = MemoryTools.__new__(MemoryTools)
    mem.sqlite_path = str(tmp_path / "pi.db")
    mem.supabase = MagicMock()
    mem._sqlite_backend = InMemoryStorageBackend()
    mem._init_sqlite()

    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    convs = mem.list_conversations()
    assert any(c["id"] == "cv1" for c in convs)


def test_backend_injection_isolates_tests():
    """Two InMemoryStorageBackend instances are fully independent."""
    from agent.storage import InMemoryStorageBackend
    b1 = InMemoryStorageBackend()
    b2 = InMemoryStorageBackend()
    c1 = b1.connect()
    c1.execute("CREATE TABLE t (x TEXT)")
    c1.commit()
    c1.close()

    c2 = b2.connect()
    tables = {r[0] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    c2.close()
    assert "t" not in tables


# ── StorageBackend protocol completeness ──────────────────────────────────────

def test_storage_backend_has_connect_method():
    from agent.storage import StorageBackend
    assert hasattr(StorageBackend, "connect")


def test_sqlite_backend_stores_path(tmp_path):
    from agent.storage import SQLiteStorageBackend
    path = str(tmp_path / "test.db")
    b = SQLiteStorageBackend(path)
    assert b.sqlite_path == path
