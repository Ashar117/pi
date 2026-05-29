"""testing/test_observability.py — T-103: agent/observability.py contract."""
import os
import sys
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Isolate each test to its own temp DB ──────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Reset the observability module's connection + DB path for each test."""
    import agent.observability as obs
    test_db = tmp_path / "silent_failures.db"
    with (
        patch.object(obs, "_DB_PATH", test_db),
        patch.object(obs, "_conn", None),
        patch.object(obs, "_insert_count", 0),
    ):
        yield test_db
    # ensure connection is cleared even if test raised
    obs._conn = None


# ── Core insert ───────────────────────────────────────────────────────────────

def test_track_silent_records(fresh_db):
    from agent.observability import track_silent
    track_silent("memory.test", ValueError("boom"))
    conn = sqlite3.connect(str(fresh_db))
    rows = conn.execute("SELECT category, exception_type FROM silent_failures").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0] == ("memory.test", "ValueError")


def test_track_silent_never_raises(fresh_db):
    import agent.observability as obs
    from agent.observability import track_silent

    def bad_connect(*a, **kw):
        raise OSError("disk full")

    with patch("sqlite3.connect", bad_connect):
        obs._conn = None  # force reconnect attempt
        # must not raise
        track_silent("anything", RuntimeError("x"))


# ── Aggregation ───────────────────────────────────────────────────────────────

def test_recent_failures_aggregates(fresh_db):
    from agent.observability import track_silent, recent_failures
    track_silent("cat.a", ValueError("1"))
    track_silent("cat.a", ValueError("2"))
    track_silent("cat.b", KeyError("k"))
    track_silent("cat.c", RuntimeError("r"))
    result = recent_failures(24)
    assert result["cat.a"] == 2
    assert result["cat.b"] == 1
    assert result["cat.c"] == 1


def test_recent_failures_time_window(fresh_db):
    from agent.observability import recent_failures
    import agent.observability as obs

    # Insert one row with a timestamp 48h in the past manually
    conn = sqlite3.connect(str(fresh_db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS silent_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            category TEXT NOT NULL,
            exception_type TEXT NOT NULL,
            redacted_message TEXT,
            context_json TEXT
        );
    """)
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    conn.execute(
        "INSERT INTO silent_failures (timestamp, category, exception_type) VALUES (?,?,?)",
        (old_ts, "old.cat", "OldError"),
    )
    conn.commit()
    conn.close()
    obs._conn = None  # re-open so module uses the same file

    # The old row should not appear in a 24h window
    result = recent_failures(24)
    assert result.get("old.cat", 0) == 0


# ── Eviction ──────────────────────────────────────────────────────────────────

def test_eviction_at_cap(fresh_db):
    from agent.observability import track_silent, cleanup_old
    import agent.observability as obs

    # Insert 10005 rows directly
    obs._conn = None
    conn = sqlite3.connect(str(fresh_db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS silent_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            category TEXT NOT NULL,
            exception_type TEXT NOT NULL,
            redacted_message TEXT,
            context_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_failures_cat_time
            ON silent_failures (category, timestamp DESC);
    """)
    ts = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "INSERT INTO silent_failures (timestamp, category, exception_type) VALUES (?,?,?)",
        [(ts, "bulk", "BulkError")] * 10005,
    )
    conn.commit()
    conn.close()
    obs._conn = None  # let module reopen

    deleted = cleanup_old(10000)
    assert deleted == 5

    conn2 = sqlite3.connect(str(fresh_db))
    (count,) = conn2.execute("SELECT COUNT(*) FROM silent_failures").fetchone()
    conn2.close()
    assert count <= 10000


# ── Redaction ─────────────────────────────────────────────────────────────────

def test_redaction_applied(fresh_db):
    from agent.observability import track_silent
    e = FileNotFoundError(r"missing e:\pi\.env file")
    track_silent("files.missing", e)
    conn = sqlite3.connect(str(fresh_db))
    row = conn.execute("SELECT redacted_message FROM silent_failures").fetchone()
    conn.close()
    assert row is not None
    # safe_error with audience='public_log' returns just the type name
    assert "FileNotFoundError" in row[0]
    assert r"e:\pi" not in (row[0] or "")


# ── Thread safety ─────────────────────────────────────────────────────────────

def test_thread_safety(fresh_db):
    from agent.observability import track_silent, recent_failures
    errors = []

    def worker():
        try:
            track_silent("thread.test", ValueError("concurrent"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Threads raised: {errors}"
    result = recent_failures(1)
    assert result.get("thread.test", 0) == 10


# ── Context JSON ──────────────────────────────────────────────────────────────

def test_context_json_roundtrip(fresh_db):
    import json
    from agent.observability import track_silent
    track_silent("ctx.test", None, context={"k": "v", "n": 42})
    conn = sqlite3.connect(str(fresh_db))
    row = conn.execute("SELECT context_json FROM silent_failures").fetchone()
    conn.close()
    assert row is not None
    data = json.loads(row[0])
    assert data == {"k": "v", "n": 42}


# ── system_introspect integration ─────────────────────────────────────────────

def test_system_introspect_includes_silent_failures(fresh_db):
    from unittest.mock import MagicMock
    from agent.tools import _system_introspect
    from agent.observability import track_silent
    track_silent("introspect.test", ValueError("x"))

    agent = MagicMock()
    agent.session_id = "test-session"
    agent.mode = "normie"
    agent.session_start = datetime.now(timezone.utc)
    agent.memory.sqlite_path = str(fresh_db)  # won't have l3_cache but won't crash

    result = _system_introspect(agent)
    assert "silent_failures_24h" in result
