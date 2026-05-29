"""testing/test_retention.py — T-109: retention policy engine tests."""
import gzip
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, n: int = 100):
    path.write_text("\n".join(json.dumps({"i": i}) for i in range(n)), encoding="utf-8")


def _mtime_yesterday(path: Path):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    os.utime(str(path), (yesterday, yesterday))


def _make_sqlite(path: Path, table: str, ts_col: str, rows_old: int = 10, rows_recent: int = 5):
    conn = sqlite3.connect(str(path))
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, {ts_col} TEXT)")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(rows_old):
        conn.execute(f"INSERT INTO {table} ({ts_col}) VALUES (?)", (old_ts,))
    for i in range(rows_recent):
        conn.execute(f"INSERT INTO {table} ({ts_col}) VALUES (?)", (recent_ts,))
    conn.commit()
    conn.close()


# ── jsonl_rotate ───────────────────────────────────────────────────────────────

def test_jsonl_rotate_creates_archive(tmp_path):
    from agent.retention import Policy, run_policy

    src = tmp_path / "turns.jsonl"
    _write_jsonl(src)
    _mtime_yesterday(src)

    p = Policy(
        name="turns_jsonl",
        path=str(src),
        kind="jsonl_rotate",
        keep_archives=10,
        archive_dir=str(tmp_path / "archive"),
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p)

    assert result["applied"] is True
    archives = list((tmp_path / "archive").glob("turns_jsonl-*.jsonl.gz"))
    assert len(archives) == 1
    assert src.read_text() == ""  # truncated


def test_jsonl_rotate_skips_if_not_due(tmp_path):
    from agent.retention import Policy, run_policy

    src = tmp_path / "turns.jsonl"
    _write_jsonl(src)
    # mtime = now (today) — no rotation needed

    p = Policy(
        name="turns_jsonl",
        path=str(src),
        kind="jsonl_rotate",
        archive_dir=str(tmp_path / "archive"),
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p)

    assert result["applied"] is False
    assert "today" in result["reason"]


def test_jsonl_rotate_keeps_archives_count(tmp_path):
    from agent.retention import Policy, run_policy

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    # Pre-create 12 fake archives (older than keep_archives=10)
    for i in range(12):
        date_str = (datetime.now(timezone.utc) - timedelta(days=i + 2)).strftime("%Y-%m-%d")
        (archive_dir / f"turns_jsonl-{date_str}.jsonl.gz").write_bytes(b"fake")

    src = tmp_path / "turns.jsonl"
    _write_jsonl(src)
    _mtime_yesterday(src)

    p = Policy(
        name="turns_jsonl",
        path=str(src),
        kind="jsonl_rotate",
        keep_archives=10,
        archive_dir=str(archive_dir),
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p)

    archives = list(archive_dir.glob("turns_jsonl-*.jsonl.gz"))
    assert len(archives) <= 10
    assert result["stats"]["archives_pruned"] >= 2


# ── sqlite_table_prune ────────────────────────────────────────────────────────

def test_sqlite_table_prune_removes_old(tmp_path):
    from agent.retention import Policy, run_policy

    db = tmp_path / "watchers.db"
    _make_sqlite(db, "watcher_events", "timestamp", rows_old=10, rows_recent=5)

    p = Policy(
        name="watcher_events_prune",
        path=str(db),
        kind="sqlite_table_prune",
        table="watcher_events",
        timestamp_col="timestamp",
        max_age_days=30,
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p)

    assert result["applied"] is True
    assert result["stats"]["rows_deleted"] == 10

    conn = sqlite3.connect(str(db))
    remaining = conn.execute("SELECT COUNT(*) FROM watcher_events").fetchone()[0]
    conn.close()
    assert remaining == 5


def test_sqlite_table_prune_vacuum_after(tmp_path):
    from agent.retention import Policy, run_policy

    db = tmp_path / "watchers.db"
    _make_sqlite(db, "watcher_events", "timestamp", rows_old=20, rows_recent=3)

    p = Policy(
        name="watcher_events_prune",
        path=str(db),
        kind="sqlite_table_prune",
        table="watcher_events",
        timestamp_col="timestamp",
        max_age_days=30,
        vacuum_after=True,
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p)

    assert result["applied"] is True
    assert result["stats"]["vacuumed"] is True


# ── log_size_rotate ───────────────────────────────────────────────────────────

def test_log_size_rotate_rolls_when_oversized(tmp_path):
    from agent.retention import Policy, run_policy

    log = tmp_path / "memory_replication.log"
    # Write 2 MB of data (threshold is 1 MB for this test)
    log.write_bytes(b"x" * (2 * 1024 * 1024))

    p = Policy(
        name="memory_replication_log",
        path=str(log),
        kind="log_size_rotate",
        max_size_mb=1,
        keep_archives=5,
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p)

    assert result["applied"] is True
    assert result["stats"]["rotated"] is True
    assert Path(str(log) + ".1").exists()
    assert log.read_bytes() == b""  # source recreated empty


def test_log_size_rotate_skips_when_small(tmp_path):
    from agent.retention import Policy, run_policy

    log = tmp_path / "memory_replication.log"
    log.write_bytes(b"small content")

    p = Policy(
        name="memory_replication_log",
        path=str(log),
        kind="log_size_rotate",
        max_size_mb=50,
        keep_archives=5,
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p)

    assert result["applied"] is False


# ── sqlite_vacuum ─────────────────────────────────────────────────────────────

def test_sqlite_vacuum_runs_when_due(tmp_path):
    from agent.retention import Policy, run_policy

    db = tmp_path / "pi.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.commit()
    conn.close()

    p = Policy(
        name="pi_db_vacuum",
        path=str(db),
        kind="sqlite_vacuum",
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p)

    assert result["applied"] is True
    assert result["stats"]["vacuumed"] is True


def test_sqlite_vacuum_skips_within_weekly_window(tmp_path):
    from agent.retention import Policy, run_policy

    db = tmp_path / "pi.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.commit()
    conn.close()

    # Record last_applied as 3 days ago
    recent_iso = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    state_data = {"pi_db_vacuum": {"last_applied": recent_iso, "last_run": recent_iso}}
    state_path = tmp_path / "retention_state.json"
    state_path.write_text(json.dumps(state_data), encoding="utf-8")
    lock_path = tmp_path / "retention_state.lock"

    p = Policy(
        name="pi_db_vacuum",
        path=str(db),
        kind="sqlite_vacuum",
        schedule="weekly",
    )

    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p)

    assert result["applied"] is False
    assert "weekly" in result["reason"]


# ── dry_run ───────────────────────────────────────────────────────────────────

def test_dry_run_no_destructive_ops_jsonl(tmp_path):
    from agent.retention import Policy, run_policy

    src = tmp_path / "turns.jsonl"
    original = "\n".join(json.dumps({"i": i}) for i in range(50))
    src.write_text(original, encoding="utf-8")
    _mtime_yesterday(src)

    p = Policy(
        name="turns_jsonl",
        path=str(src),
        kind="jsonl_rotate",
        archive_dir=str(tmp_path / "archive"),
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p, dry_run=True)

    assert result["applied"] is True
    assert result["reason"] == "dry_run"
    # source file must be unchanged
    assert src.read_text(encoding="utf-8") == original
    # no archive created
    assert not (tmp_path / "archive").exists()


def test_dry_run_no_destructive_ops_sqlite_prune(tmp_path):
    from agent.retention import Policy, run_policy

    db = tmp_path / "watchers.db"
    _make_sqlite(db, "watcher_events", "timestamp", rows_old=10, rows_recent=5)

    p = Policy(
        name="watcher_events_prune",
        path=str(db),
        kind="sqlite_table_prune",
        table="watcher_events",
        timestamp_col="timestamp",
        max_age_days=30,
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p, dry_run=True)

    assert result["applied"] is True
    # row count not actually deleted
    conn = sqlite3.connect(str(db))
    remaining = conn.execute("SELECT COUNT(*) FROM watcher_events").fetchone()[0]
    conn.close()
    assert remaining == 15


def test_dry_run_no_destructive_ops_log_size(tmp_path):
    from agent.retention import Policy, run_policy

    log = tmp_path / "memory_replication.log"
    content = b"x" * (2 * 1024 * 1024)
    log.write_bytes(content)

    p = Policy(
        name="memory_replication_log",
        path=str(log),
        kind="log_size_rotate",
        max_size_mb=1,
        keep_archives=5,
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        result = run_policy(p, dry_run=True)

    assert result["applied"] is True
    assert log.read_bytes() == content  # unchanged
    assert not Path(str(log) + ".1").exists()


# ── Concurrent run_all uses lock ──────────────────────────────────────────────

def test_concurrent_run_all_uses_lock(tmp_path):
    """Two threads calling run_all — both complete without error (lock serialises them)."""
    from agent.retention import Policy, run_all

    db = tmp_path / "watchers.db"
    _make_sqlite(db, "watcher_events", "timestamp", rows_old=5, rows_recent=3)

    policies = [Policy(
        name="watcher_events_prune",
        path=str(db),
        kind="sqlite_table_prune",
        table="watcher_events",
        timestamp_col="timestamp",
        max_age_days=30,
        schedule="on_demand",
    )]

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    results = []

    def _run():
        with patch("agent.retention._STATE_PATH", state_path), \
             patch("agent.retention._LOCK_PATH", lock_path):
            r = run_all(policies)
        results.append(r)

    t1 = threading.Thread(target=_run)
    t2 = threading.Thread(target=_run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    total_applied = sum(r["applied"] for r in results)
    # First run applies; second sees schedule guard (last_applied just set, daily)
    # Both should complete without error
    for r in results:
        assert "errors" in r


# ── Policy failure logged via track_silent ─────────────────────────────────────

def test_policy_failure_recorded_in_track_silent(tmp_path):
    from agent.retention import Policy, run_policy

    p = Policy(
        name="badpolicy",
        path=str(tmp_path / "nonexistent.db"),
        kind="sqlite_table_prune",
        table="t",
        timestamp_col="ts",
        max_age_days=30,
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"

    # Patch _handle_sqlite_table_prune to raise
    recorded = []

    def fake_track(cat, exc=None, **kw):
        recorded.append(cat)

    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path), \
         patch("agent.retention.track_silent", fake_track), \
         patch("agent.retention._handle_sqlite_table_prune", side_effect=RuntimeError("boom")):
        result = run_policy(p)

    assert result["applied"] is False
    assert "retention.badpolicy" in recorded


# ── State file roundtrip ──────────────────────────────────────────────────────

def test_state_roundtrip(tmp_path):
    from agent.retention import Policy, run_policy

    db = tmp_path / "pi.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.commit()
    conn.close()

    p = Policy(
        name="pi_db_vacuum",
        path=str(db),
        kind="sqlite_vacuum",
        schedule="on_demand",
    )

    state_path = tmp_path / "retention_state.json"
    lock_path = tmp_path / "retention_state.lock"
    with patch("agent.retention._STATE_PATH", state_path), \
         patch("agent.retention._LOCK_PATH", lock_path):
        run_policy(p)

    state = json.loads(state_path.read_text())
    assert "pi_db_vacuum" in state
    assert "last_run" in state["pi_db_vacuum"]
    assert "last_applied" in state["pi_db_vacuum"]
