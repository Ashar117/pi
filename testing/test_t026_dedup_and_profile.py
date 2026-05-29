"""
testing/test_t026_dedup_and_profile.py — T-026b and T-026c failing tests.

T-026b: _is_l3_duplicate misses duplicates when content has trailing markers
        (marker_abc123, unique7x4b) — the 120-char prefix includes the marker,
        so two marker-appended versions of the same fact look different.

T-026c: profile_structured writes must merge into a single L3 entry; currently
        two writes create two rows.

All offline — Supabase mocked, SQLite real (temp file).
"""
import json
import os
import sqlite3
import tempfile
import pytest
from unittest.mock import MagicMock


# ── shared helpers (same pattern as test_memory_tools_gaps.py) ────────────────

def _make_memory():
    import threading
    from tools.tools_memory import MemoryTools
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    supa = MagicMock()
    mt = MemoryTools.__new__(MemoryTools)
    mt.supabase = supa
    mt.sqlite_path = tmp.name
    mt._last_sync = None
    mt._sync_ttl_seconds = 300
    mt._sync_lock = threading.Lock()
    mt._supa_lock = threading.RLock()
    mt._init_sqlite()
    return mt, supa, tmp.name


def _seed_l3(db, rows):
    conn = sqlite3.connect(db)
    c = conn.cursor()
    for row in rows:
        c.execute(
            "INSERT INTO l3_cache (id, content, importance, category, active_until, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [row["id"], row["content"], row.get("importance", 5),
             row.get("category", "note"), row.get("active_until"),
             row.get("created_at", "2026-01-01T00:00:00Z")],
        )
    conn.commit()
    conn.close()


def _l3_count(db, category=None):
    conn = sqlite3.connect(db)
    c = conn.cursor()
    if category:
        c.execute("SELECT COUNT(*) FROM l3_cache WHERE category=?", [category])
    else:
        c.execute("SELECT COUNT(*) FROM l3_cache")
    n = c.fetchone()[0]
    conn.close()
    return n


# ─────────────────────────────────────────────────────────────────────────────
# T-026b: marker-aware dedup
# ─────────────────────────────────────────────────────────────────────────────

BASE_FACT = "Ash's subway order: oregano bread, chicken, no sauce, extra veggies."


def test_l3_dedup_catches_same_content_no_marker():
    """Baseline: exact same content (no markers) is already deduped correctly."""
    mt, supa, db = _make_memory()
    _seed_l3(db, [{"id": "orig", "content": BASE_FACT, "category": "preferences"}])
    result = mt._is_l3_duplicate(BASE_FACT, "preferences")
    assert result == "orig", "Same content with no marker should match"
    os.unlink(db)


def test_l3_dedup_misses_marker_appended_duplicate():
    """T-026b BUG: different trailing markers fool the 120-char prefix check."""
    mt, supa, db = _make_memory()
    stored = f"{BASE_FACT} marker_abc12345"
    incoming = f"{BASE_FACT} marker_def67890"

    _seed_l3(db, [{"id": "orig", "content": stored, "category": "preferences"}])
    result = mt._is_l3_duplicate(incoming, "preferences")

    # Currently returns None (dedup missed); fix should return "orig"
    assert result == "orig", (
        f"_is_l3_duplicate should strip markers and match, but returned {result!r}. "
        f"Stored: {stored!r}  Incoming: {incoming!r}"
    )
    os.unlink(db)


def test_l3_dedup_misses_unique_tag_appended_duplicate():
    """T-026b BUG: 'unique7x4b' style tags also fool the prefix check."""
    mt, supa, db = _make_memory()
    stored = f"{BASE_FACT} unique7x4b"
    incoming = f"{BASE_FACT} uniqueXX99"

    _seed_l3(db, [{"id": "orig", "content": stored, "category": "preferences"}])
    result = mt._is_l3_duplicate(incoming, "preferences")

    assert result == "orig", (
        f"_is_l3_duplicate should strip unique-tags and match, but returned {result!r}"
    )
    os.unlink(db)


def test_l3_dedup_memory_write_skips_marker_variant(monkeypatch):
    """T-026b BUG: writing the same fact with different markers creates two rows."""
    mt, supa, db = _make_memory()

    # Silence Supabase insert — we only care about SQLite
    supa.table.return_value.insert.return_value.execute.return_value = MagicMock()
    supa.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": "orig", "content": f"{BASE_FACT} marker_abc12345"}
    ]

    # Pre-seed the SQLite cache as if first write already happened
    _seed_l3(db, [{"id": "orig", "content": f"{BASE_FACT} marker_abc12345", "category": "preferences"}])

    # Second write with different marker — should be deduped
    result = mt.memory_write(
        content=f"{BASE_FACT} marker_def67890",
        tier="l3",
        category="preferences",
        importance=7,
    )

    assert result.get("duplicate") is True, (
        f"Second marker-variant write should have been deduped, got: {result}"
    )
    # Only one row in SQLite
    assert _l3_count(db, "preferences") == 1, (
        f"Expected 1 row in l3_cache, got {_l3_count(db, 'preferences')}"
    )
    os.unlink(db)


# ─────────────────────────────────────────────────────────────────────────────
# T-026c: structured profile — merge semantics
# ─────────────────────────────────────────────────────────────────────────────

PROFILE_WRITE_1 = json.dumps({"name": "TestUser", "school": "StateU", "program": "CS"})
PROFILE_WRITE_2 = json.dumps({"school": "StateU", "visa": "student", "advisor": "Smith"})
PROFILE_MERGED  = {"name": "TestUser", "school": "StateU", "program": "CS",
                   "visa": "student", "advisor": "Smith"}


def test_profile_structured_second_write_returns_existing_id():
    """T-026c BUG: second profile_structured write must merge, not insert new row."""
    mt, supa, db = _make_memory()

    supa.table.return_value.insert.return_value.execute.return_value = MagicMock()
    # _verify_write: make first write succeed
    supa.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": "profile-1"}
    ]

    # First write
    r1 = mt.memory_write(PROFILE_WRITE_1, tier="l3", category="profile_structured", importance=9)
    assert r1["success"] is True

    # Second write — must detect existing profile and merge rather than insert
    r2 = mt.memory_write(PROFILE_WRITE_2, tier="l3", category="profile_structured", importance=9)
    assert r2.get("duplicate") is True or r2.get("merged") is True, (
        f"Second profile_structured write should merge into existing entry, got: {r2}"
    )

    # Only one row in the cache
    assert _l3_count(db, "profile_structured") == 1, (
        f"Expected 1 profile_structured row, got {_l3_count(db, 'profile_structured')}"
    )
    os.unlink(db)


def test_profile_structured_merged_content_has_all_keys():
    """T-026c BUG: merged profile row must contain keys from both writes."""
    mt, supa, db = _make_memory()

    supa.table.return_value.insert.return_value.execute.return_value = MagicMock()
    supa.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    supa.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": "profile-1"}
    ]

    mt.memory_write(PROFILE_WRITE_1, tier="l3", category="profile_structured", importance=9)
    mt.memory_write(PROFILE_WRITE_2, tier="l3", category="profile_structured", importance=9)

    conn = sqlite3.connect(db)
    c = conn.cursor()
    c.execute("SELECT content FROM l3_cache WHERE category='profile_structured'")
    rows = c.fetchall()
    conn.close()

    assert len(rows) == 1, f"Expected 1 profile row, got {len(rows)}"
    stored = json.loads(rows[0][0])
    for key in PROFILE_MERGED:
        assert key in stored, (
            f"Merged profile missing key {key!r}. Stored: {stored}"
        )
    os.unlink(db)
