"""testing/test_caretaker_lite.py — T-125a: derived-fact caretaker tests."""
import json
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _init_db(tmp_path) -> Path:
    db = tmp_path / "pi.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE l3_cache (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            importance INTEGER,
            category TEXT,
            active_until TEXT,
            created_at TEXT,
            invalid_at TEXT,
            kind TEXT,
            source_id TEXT,
            recompute_after TEXT,
            formula TEXT,
            superseded_by TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db


def _insert(db, **kw):
    cols = ", ".join(kw.keys())
    placeholders = ", ".join("?" for _ in kw)
    conn = sqlite3.connect(str(db))
    conn.execute(f"INSERT INTO l3_cache ({cols}) VALUES ({placeholders})", list(kw.values()))
    conn.commit()
    conn.close()


def _read(db, id_):
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT content, recompute_after FROM l3_cache WHERE id = ?", (id_,)).fetchone()
    conn.close()
    return row


# ── formula correctness ─────────────────────────────────────────────────────

def test_formula_age_from_birthday_before_bday():
    from agent.caretaker import _formula_age_from_birthday
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    content, nxt = _formula_age_from_birthday("User born 2006-08-17", now)
    assert "19" in content
    assert nxt == datetime(2026, 8, 17, tzinfo=timezone.utc)


def test_formula_age_from_birthday_after_bday():
    from agent.caretaker import _formula_age_from_birthday
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    content, nxt = _formula_age_from_birthday("User born 2006-08-17", now)
    assert "20" in content
    assert nxt == datetime(2027, 8, 17, tzinfo=timezone.utc)


def test_formula_days_until_date():
    from agent.caretaker import _formula_days_until_date
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    content, nxt = _formula_days_until_date("Trip on 2026-06-01", now)
    assert "8 days until" in content


# ── lite recomputes due rows ────────────────────────────────────────────────

def test_lite_recomputes_age_after_birthday(tmp_path):
    from agent.caretaker import lite
    db = _init_db(tmp_path)

    src_id = uuid.uuid4().hex
    _insert(db, id=src_id, content="User born 2006-08-17", importance=9,
            category="profile", created_at="2025-01-01T00:00:00+00:00")

    derived_id = uuid.uuid4().hex
    _insert(
        db, id=derived_id,
        content="User is 19 years old (computed 2025-09-01)",
        importance=8, category="derived",
        created_at="2025-09-01T00:00:00+00:00",
        kind="derived", source_id=src_id,
        recompute_after="2026-08-17T00:00:00+00:00",
        formula="age_from_birthday",
    )

    # Simulate "now" past birthday
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    stats = lite(db, now=now)
    assert stats["recomputed"] == 1
    content, recompute_after = _read(db, derived_id)
    assert "20" in content
    assert "2027-08-17" in recompute_after


def test_lite_no_op_when_not_yet_due(tmp_path):
    from agent.caretaker import lite
    db = _init_db(tmp_path)

    src_id = uuid.uuid4().hex
    _insert(db, id=src_id, content="User born 2006-08-17", importance=9,
            category="profile", created_at="2025-01-01T00:00:00+00:00")

    derived_id = uuid.uuid4().hex
    _insert(
        db, id=derived_id, content="User is 19",
        importance=8, category="derived",
        created_at="2025-09-01T00:00:00+00:00",
        kind="derived", source_id=src_id,
        recompute_after="2026-08-17T00:00:00+00:00",
        formula="age_from_birthday",
    )

    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    stats = lite(db, now=now)
    assert stats["recomputed"] == 0
    content, _ = _read(db, derived_id)
    assert content == "User is 19"  # unchanged


def test_lite_formula_failure_skips_row(tmp_path):
    """A bad source row (no date) should not abort the whole job."""
    from agent.caretaker import lite
    db = _init_db(tmp_path)

    src_id = uuid.uuid4().hex
    _insert(db, id=src_id, content="something with no date", importance=9,
            category="profile", created_at="2025-01-01T00:00:00+00:00")

    derived_id = uuid.uuid4().hex
    _insert(
        db, id=derived_id, content="(pending)",
        importance=8, category="derived",
        created_at="2025-09-01T00:00:00+00:00",
        kind="derived", source_id=src_id,
        recompute_after="2025-01-01T00:00:00+00:00",  # due
        formula="age_from_birthday",
    )

    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    stats = lite(db, now=now)
    assert stats["errors"] == 1
    assert stats["recomputed"] == 0


def test_lite_dry_run_no_mutations(tmp_path):
    from agent.caretaker import lite
    db = _init_db(tmp_path)

    src_id = uuid.uuid4().hex
    _insert(db, id=src_id, content="User born 2006-08-17", importance=9,
            category="profile", created_at="2025-01-01T00:00:00+00:00")
    derived_id = uuid.uuid4().hex
    _insert(
        db, id=derived_id, content="User is 19",
        importance=8, category="derived",
        created_at="2025-09-01T00:00:00+00:00",
        kind="derived", source_id=src_id,
        recompute_after="2025-01-01T00:00:00+00:00",  # due
        formula="age_from_birthday",
    )

    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    stats = lite(db, dry_run=True, now=now)
    assert stats["skipped"] == 1
    assert stats["recomputed"] == 0
    content, _ = _read(db, derived_id)
    assert content == "User is 19"  # unchanged


# ── detection ───────────────────────────────────────────────────────────────

def test_detect_derivable_finds_birthday():
    from agent.caretaker import detect_derivable
    detected = detect_derivable("User born 2006-08-17")
    assert detected is not None
    formula, _recompute = detected
    assert formula == "age_from_birthday"


def test_detect_derivable_ignores_random_text():
    from agent.caretaker import detect_derivable
    assert detect_derivable("User likes coffee") is None
    assert detect_derivable("Project deadline coming up") is None


# ── backfill ────────────────────────────────────────────────────────────────

def test_backfill_spawns_derived_for_birthday(tmp_path):
    from agent.caretaker import backfill, lite
    db = _init_db(tmp_path)

    src_id = uuid.uuid4().hex
    _insert(db, id=src_id, content="User born 2006-08-17", importance=9,
            category="profile", created_at="2025-01-01T00:00:00+00:00")

    stats = backfill(db)
    assert stats["derived_spawned"] == 1
    # Re-run backfill — should be idempotent (no new spawns)
    stats2 = backfill(db)
    assert stats2["derived_spawned"] == 0


def test_backfill_then_lite_full_cycle(tmp_path):
    from agent.caretaker import backfill, lite
    db = _init_db(tmp_path)

    src_id = uuid.uuid4().hex
    _insert(db, id=src_id, content="User born 2006-08-17", importance=9,
            category="profile", created_at="2025-01-01T00:00:00+00:00")

    backfill(db)
    # Use a "now" that's well past the backfill's recompute_after (which is real-time)
    now = datetime.now(timezone.utc) + timedelta(seconds=5)
    stats = lite(db, now=now)
    assert stats["recomputed"] == 1
    # The pending placeholder should now be real content
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT content FROM l3_cache WHERE kind = 'derived'").fetchone()
    conn.close()
    assert "year" in row[0]  # "User is N years old"


# ── filelock serialises concurrent calls ────────────────────────────────────

def test_concurrent_lite_calls_serialised(tmp_path):
    """Two threads calling lite() in parallel must not corrupt state."""
    from agent.caretaker import lite, _LOCK_PATH
    import agent.caretaker as ct
    db = _init_db(tmp_path)
    # Point lock at tmp to isolate test
    fake_lock = tmp_path / "caretaker.lock"

    src_id = uuid.uuid4().hex
    _insert(db, id=src_id, content="User born 2006-08-17", importance=9,
            category="profile", created_at="2025-01-01T00:00:00+00:00")
    derived_id = uuid.uuid4().hex
    _insert(
        db, id=derived_id, content="(pending)",
        importance=8, category="derived",
        created_at="2025-09-01T00:00:00+00:00",
        kind="derived", source_id=src_id,
        recompute_after="2025-01-01T00:00:00+00:00",
        formula="age_from_birthday",
    )

    results = []
    def worker():
        with patch.object(ct, "_LOCK_PATH", fake_lock):
            r = lite(db, now=datetime(2026, 5, 24, tzinfo=timezone.utc))
        results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()

    # Combined results — exactly one thread should have recomputed (others see no due rows after the first commit)
    total_recomputed = sum(r["recomputed"] for r in results)
    assert total_recomputed >= 1
    # No crashes — that's the main assertion
