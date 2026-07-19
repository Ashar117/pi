"""
testing/test_memory_tools_gaps.py — Unit tests for T-026 additions to MemoryTools.

Tests: _is_l2_duplicate, prune_l3_expired, promote_l2_to_l3, L2 dedup path
       in memory_write().

All offline — Supabase is replaced by a MagicMock so no network calls are made.
SQLite is backed by a real in-memory DB spun up per test.

Run:  python -m pytest testing/test_memory_tools_gaps.py -v
"""
import sqlite3
import tempfile
import os
import pytest
from unittest.mock import MagicMock, call
from datetime import datetime, timezone, timedelta


# ── Fixture: MemoryTools with mocked Supabase and temp SQLite ─────────────────

def _make_memory():
    """Return a MemoryTools instance with a mocked Supabase and a temp SQLite file."""
    from tools.tools_memory import MemoryTools

    # Temp SQLite so we don't touch the real DB
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()

    import threading
    supabase_mock = MagicMock()
    mt = MemoryTools.__new__(MemoryTools)
    mt.supabase = supabase_mock
    mt.sqlite_path = tmp.name
    mt._last_sync = None
    mt._sync_ttl_seconds = 300
    mt._sync_lock = threading.Lock()
    mt._supa_lock = threading.RLock()
    mt._init_sqlite()

    return mt, supabase_mock, tmp.name


def _seed_l3_cache(sqlite_path, rows):
    """Directly insert rows into l3_cache for dedup testing."""
    conn = sqlite3.connect(sqlite_path)
    cursor = conn.cursor()
    for row in rows:
        cursor.execute(
            "INSERT INTO l3_cache (id, content, importance, category, active_until, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [row["id"], row["content"], row.get("importance", 5),
             row.get("category", "note"), row.get("active_until"), row.get("created_at", "2026-01-01T00:00:00Z")],
        )
    conn.commit()
    conn.close()


# ── _is_l2_duplicate ──────────────────────────────────────────────────────────

def test_is_l2_duplicate_found():
    mt, supa, db = _make_memory()
    existing_text = "User prefers dark mode for all applications"
    supa.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": "abc123", "title": existing_text[:100]}
    ]
    result = mt._is_l2_duplicate(existing_text, "permanent_profile")
    assert result == "abc123"
    os.unlink(db)


def test_is_l2_duplicate_different_category_not_matched():
    mt, supa, db = _make_memory()
    # Returns rows for category=note only
    supa.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    result = mt._is_l2_duplicate("User prefers dark mode", "permanent_profile")
    assert result is None
    os.unlink(db)


def test_is_l2_duplicate_supabase_error_returns_none():
    mt, supa, db = _make_memory()
    supa.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = RuntimeError("network error")
    result = mt._is_l2_duplicate("anything", "note")
    assert result is None
    os.unlink(db)


def test_is_l2_duplicate_empty_content_returns_none():
    mt, supa, db = _make_memory()
    result = mt._is_l2_duplicate("", "note")
    assert result is None
    os.unlink(db)


# ── memory_write L2 dedup path ────────────────────────────────────────────────

def test_memory_write_l2_skips_duplicate():
    mt, supa, db = _make_memory()
    # Stub _is_l2_duplicate to return an existing id
    mt._is_l2_duplicate = MagicMock(return_value="existing-id-123")

    result = mt.memory_write("User likes Python", tier="l2", category="permanent_profile")

    assert result["duplicate"] is True
    assert result["id"] == "existing-id-123"
    assert result["tier"] == "l2"
    # Supabase insert should NOT have been called
    supa.table.return_value.insert.assert_not_called()
    os.unlink(db)


def test_memory_write_l2_writes_when_no_duplicate():
    mt, supa, db = _make_memory()
    mt._is_l2_duplicate = MagicMock(return_value=None)
    supa.table.return_value.insert.return_value.execute.return_value = MagicMock()

    result = mt.memory_write("New unique fact", tier="l2", category="note")

    assert result["success"] is True
    assert result.get("duplicate") is None
    supa.table.return_value.insert.assert_called_once()
    os.unlink(db)


# ── prune_l3_expired ──────────────────────────────────────────────────────────

def test_prune_l3_expired_deletes_from_supabase_and_sqlite():
    mt, supa, db = _make_memory()

    # Seed SQLite with one expired and one non-expired entry
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    _seed_l3_cache(db, [
        {"id": "expired-1", "content": "old fact", "active_until": past, "category": "note"},
        {"id": "active-1", "content": "current fact", "active_until": future, "category": "note"},
        {"id": "permanent", "content": "no expiry", "active_until": None, "category": "note"},
    ])

    # Stub Supabase delete chain
    supa.table.return_value.delete.return_value.lt.return_value.not_.is_.return_value.execute.return_value.data = [{"id": "expired-1"}]

    result = mt.prune_l3_expired()

    assert result["success"] is True
    assert result["supabase_deleted"] == 1

    # SQLite: only "expired-1" should be gone
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM l3_cache ORDER BY id")
    remaining = [r[0] for r in cursor.fetchall()]
    # T-309: "gone" means archived, not destroyed.
    archived = cursor.execute(
        "SELECT id, archive_reason FROM l3_archive WHERE id = 'expired-1'"
    ).fetchone()
    conn.close()
    assert "expired-1" not in remaining
    assert "active-1" in remaining
    assert "permanent" in remaining
    assert archived == ("expired-1", "expired")
    os.unlink(db)


def test_prune_l3_expired_supabase_error_still_prunes_sqlite():
    mt, supa, db = _make_memory()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _seed_l3_cache(db, [
        {"id": "exp-1", "content": "old", "active_until": past, "category": "note"},
    ])

    # Supabase raises
    supa.table.return_value.delete.return_value.lt.return_value.not_.is_.return_value.execute.side_effect = RuntimeError("down")

    result = mt.prune_l3_expired()

    # Still reports success from SQLite side
    assert result["success"] is True
    assert result["sqlite_deleted"] == 1

    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM l3_cache")
    assert cursor.fetchall() == []
    conn.close()
    os.unlink(db)


# ── promote_l2_to_l3 ─────────────────────────────────────────────────────────

def _l2_row(text, category="permanent_profile", importance=9):
    return {"id": "l2-id-1", "title": text[:100],
            "content": {"text": text}, "category": category, "importance": importance}


def test_promote_l2_to_l3_writes_new_facts():
    mt, supa, db = _make_memory()

    # L2 returns one high-importance candidate
    supa.table.return_value.select.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        _l2_row("User is a CS undergrad at State University")
    ]

    # _is_l3_duplicate: not in cache yet
    mt._is_l3_duplicate = MagicMock(return_value=None)

    # memory_write stub
    mt.memory_write = MagicMock(return_value={"success": True, "id": "new-l3", "tier": "l3"})

    result = mt.promote_l2_to_l3(importance_threshold=8)

    assert result["promoted"] == 1
    assert result["skipped"] == 0
    mt.memory_write.assert_called_once()
    call_kwargs = mt.memory_write.call_args.kwargs
    assert call_kwargs["tier"] == "l3"
    assert "State University" in call_kwargs["content"]
    os.unlink(db)


def test_promote_l2_to_l3_skips_existing_l3():
    mt, supa, db = _make_memory()

    supa.table.return_value.select.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        _l2_row("User is a CS undergrad at State University")
    ]

    # Already in L3
    mt._is_l3_duplicate = MagicMock(return_value="existing-l3-id")
    mt.memory_write = MagicMock()

    result = mt.promote_l2_to_l3(importance_threshold=8)

    assert result["promoted"] == 0
    assert result["skipped"] == 1
    mt.memory_write.assert_not_called()
    os.unlink(db)


def test_promote_l2_to_l3_supabase_error_returns_zero():
    mt, supa, db = _make_memory()
    supa.table.return_value.select.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.side_effect = RuntimeError("quota")

    result = mt.promote_l2_to_l3()

    assert result["promoted"] == 0
    assert result["skipped"] == 0
    os.unlink(db)


def test_promote_l2_to_l3_skips_below_threshold():
    mt, supa, db = _make_memory()

    # importance=5, threshold=8 — should not be returned by Supabase (gte filter)
    supa.table.return_value.select.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value.data = []

    mt._is_l3_duplicate = MagicMock(return_value=None)
    mt.memory_write = MagicMock()

    result = mt.promote_l2_to_l3(importance_threshold=8)

    assert result["promoted"] == 0
    mt.memory_write.assert_not_called()
    os.unlink(db)
