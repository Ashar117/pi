"""testing/test_decay.py — T-135: Ebbinghaus decay + DecayArchivePolicy tests."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.salience import effective_importance, default_decay_rate
from agent.retention import Policy, run_policy


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_db(tmp_path: Path, rows: list) -> Path:
    db = tmp_path / "pi.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE l3_cache (
            id TEXT PRIMARY KEY,
            content TEXT,
            importance INTEGER DEFAULT 5,
            category TEXT DEFAULT 'note',
            active_until TEXT,
            invalid_at TEXT,
            superseded_by TEXT,
            decay_rate REAL DEFAULT 0.01,
            pinned INTEGER DEFAULT 0,
            last_accessed_at TEXT,
            created_at TEXT DEFAULT '2026-01-01T00:00:00Z'
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO l3_cache "
            "(id, content, importance, category, active_until, invalid_at, "
            "superseded_by, decay_rate, pinned, last_accessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                r["id"], r.get("content", "test content"),
                r.get("importance", 5), r.get("category", "note"),
                r.get("active_until"), r.get("invalid_at"),
                r.get("superseded_by"),
                r.get("decay_rate", 0.01), r.get("pinned", 0),
                r.get("last_accessed_at"),
            ],
        )
    conn.commit()
    conn.close()
    return db


def _decay_policy(db_path: Path) -> Policy:
    return Policy(
        name="test_decay_archive",
        path=str(db_path),
        kind="l3_decay_archive",
        schedule="on_demand",
    )


# ── pure math tests ────────────────────────────────────────────────────────────

def test_decay_reduces_effective_importance():
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    eff = effective_importance(importance=8, decay_rate=0.01, last_accessed_iso=old)
    # 8 * exp(-1) ≈ 2.94
    assert eff < 4.0


def test_pinned_immune_to_decay():
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    eff = effective_importance(importance=8, decay_rate=0.01,
                               last_accessed_iso=old, pinned=1)
    assert eff == 8.0


def test_decay_per_category_rates_differ():
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    rate_perm = default_decay_rate("permanent_profile")
    rate_sess = default_decay_rate("session_history")
    eff_perm = effective_importance(8, rate_perm, old)
    eff_sess = effective_importance(8, rate_sess, old)
    assert eff_perm > eff_sess  # permanent_profile decays slower


def test_no_decay_when_rate_is_none():
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    eff = effective_importance(importance=5, decay_rate=None, last_accessed_iso=old)
    assert eff == 5.0


def test_no_decay_when_access_time_is_none():
    eff = effective_importance(importance=7, decay_rate=0.1, last_accessed_iso=None)
    assert eff == 7.0


# ── archive policy: env gate ───────────────────────────────────────────────────

def test_decay_archive_requires_env_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("PI_DECAY_ARCHIVE", raising=False)
    old = (datetime.now(timezone.utc) - timedelta(days=300)).isoformat()
    db = _make_db(tmp_path, [
        {"id": "aa-1", "importance": 1, "decay_rate": 0.5, "last_accessed_at": old},
    ])
    result = run_policy(_decay_policy(db))
    assert not result["applied"]
    assert "PI_DECAY_ARCHIVE" in result["reason"]


# ── archive policy: below-threshold rows archived ────────────────────────────

def test_decay_archive_moves_low_eff_importance(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_DECAY_ARCHIVE", "on")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    db = _make_db(tmp_path, [
        # 5 * exp(-0.05 * 200) = 5 * exp(-10) ≈ 0 → archive
        {"id": "bb-1", "importance": 5, "decay_rate": 0.05, "last_accessed_at": old},
        # 9 * exp(-0.001 * 200) = 9 * exp(-0.2) ≈ 7.37 → keep
        {"id": "bb-2", "importance": 9, "decay_rate": 0.001, "last_accessed_at": old},
    ])
    run_policy(_decay_policy(db))
    conn = sqlite3.connect(str(db))
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT id, active_until FROM l3_cache"
    ).fetchall()}
    conn.close()
    assert rows["bb-1"] is not None, "decayed row should be archived"
    assert rows["bb-2"] is None, "healthy row should remain active"


def test_decay_archive_spares_pinned_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_DECAY_ARCHIVE", "on")
    old = (datetime.now(timezone.utc) - timedelta(days=300)).isoformat()
    db = _make_db(tmp_path, [
        {"id": "cc-1", "importance": 3, "decay_rate": 0.1, "pinned": 1,
         "last_accessed_at": old},
    ])
    run_policy(_decay_policy(db))
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT active_until FROM l3_cache WHERE id='cc-1'").fetchone()
    conn.close()
    assert row[0] is None, "pinned row must not be archived regardless of decay"


def test_decay_archive_dry_run_no_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_DECAY_ARCHIVE", "on")
    old = (datetime.now(timezone.utc) - timedelta(days=300)).isoformat()
    db = _make_db(tmp_path, [
        {"id": "dd-1", "importance": 2, "decay_rate": 0.1, "last_accessed_at": old},
    ])
    result = run_policy(_decay_policy(db), dry_run=True)
    assert result["stats"]["archived"] >= 1
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT active_until FROM l3_cache WHERE id='dd-1'").fetchone()
    conn.close()
    assert row[0] is None, "dry_run must not modify rows"


def test_archived_rows_have_valid_active_until(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_DECAY_ARCHIVE", "on")
    old = (datetime.now(timezone.utc) - timedelta(days=300)).isoformat()
    db = _make_db(tmp_path, [
        {"id": "ee-1", "importance": 1, "decay_rate": 0.2, "last_accessed_at": old},
    ])
    run_policy(_decay_policy(db))
    conn = sqlite3.connect(str(db))
    active_until = conn.execute(
        "SELECT active_until FROM l3_cache WHERE id='ee-1'"
    ).fetchone()[0]
    conn.close()
    assert active_until is not None
    parsed = datetime.fromisoformat(active_until.replace("Z", "+00:00"))
    assert parsed <= datetime.now(timezone.utc) + timedelta(seconds=5)


def test_invalid_rows_not_processed(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_DECAY_ARCHIVE", "on")
    old = (datetime.now(timezone.utc) - timedelta(days=300)).isoformat()
    db = _make_db(tmp_path, [
        # already invalidated → should not be processed
        {"id": "ff-1", "importance": 1, "decay_rate": 0.2,
         "last_accessed_at": old, "invalid_at": "2026-01-01T00:00:00Z"},
    ])
    run_policy(_decay_policy(db))
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT active_until FROM l3_cache WHERE id='ff-1'").fetchone()
    conn.close()
    assert row[0] is None, "already-invalid rows should not be modified"
