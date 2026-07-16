"""testing/test_caretaker_contradictions.py — T-125c: contradiction scan + deep mode tests."""
import os
import sqlite3
import sys
from datetime import datetime, timezone
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


def _read_invalid(db, id_):
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT invalid_at FROM l3_cache WHERE id = ?", (id_,)).fetchone()
    conn.close()
    return row[0] if row else None


# ── topic_key extraction ─────────────────────────────────────────────────────

def test_topic_key_extracts_lives_in():
    from agent.caretaker import _topic_key
    assert _topic_key("User lives in Atlanta") == "lives_in"
    assert _topic_key("user LIVES IN Multan") == "lives_in"


def test_topic_key_extracts_works_at():
    from agent.caretaker import _topic_key
    assert _topic_key("User works at Anthropic") == "works_at"


def test_topic_key_falls_back_to_content_tokens():
    from agent.caretaker import _topic_key
    key = _topic_key("User likes coffee in the morning")
    # likes/coffee are content tokens (likes is in stopwords); coffee stays
    assert "coffee" in key


def test_value_tail_extracts_value():
    from agent.caretaker import _value_tail
    assert _value_tail("User lives in Atlanta") == "atlanta"
    assert _value_tail("user lives in Multan, Pakistan").startswith("multan")


# ── contradiction scan: lives in X then lives in Y ──────────────────────────

def test_contradicting_facts_older_invalidated(tmp_path):
    from agent.caretaker import scan_contradictions
    db = _init_db(tmp_path)
    _insert(db, id="old", content="User lives in Atlanta", importance=7,
            category="profile", created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="new", content="User lives in Multan", importance=7,
            category="profile", created_at="2026-05-01T00:00:00+00:00")

    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    stats = scan_contradictions(db, now=now)

    assert stats["conflicts_found"] == 1
    assert stats["invalidated"] == 1
    assert _read_invalid(db, "old") is not None  # marked invalid
    assert _read_invalid(db, "new") is None  # winner unchanged


# ── same value across rows → no conflict ────────────────────────────────────

def test_same_value_not_flagged(tmp_path):
    from agent.caretaker import scan_contradictions
    db = _init_db(tmp_path)
    _insert(db, id="a", content="User lives in Atlanta", importance=7,
            category="profile", created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="b", content="User lives in Atlanta", importance=5,
            category="profile", created_at="2026-02-01T00:00:00+00:00")

    stats = scan_contradictions(db)
    assert stats["conflicts_found"] == 0
    assert stats["invalidated"] == 0


# ── different categories → no conflict ──────────────────────────────────────

def test_different_categories_not_flagged(tmp_path):
    from agent.caretaker import scan_contradictions
    db = _init_db(tmp_path)
    _insert(db, id="prof", content="User lives in Atlanta", importance=7,
            category="profile", created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="trav", content="User lives in Multan", importance=7,
            category="travel", created_at="2026-05-01T00:00:00+00:00")

    stats = scan_contradictions(db)
    assert stats["conflicts_found"] == 0


# ── derived/invalidated/superseded rows skipped ──────────────────────────────

def test_invalidated_rows_skipped(tmp_path):
    from agent.caretaker import scan_contradictions
    db = _init_db(tmp_path)
    _insert(db, id="old", content="User lives in Atlanta", importance=7,
            category="profile", created_at="2026-01-01T00:00:00+00:00",
            invalid_at="2026-04-01T00:00:00+00:00")  # already invalidated
    _insert(db, id="new", content="User lives in Multan", importance=7,
            category="profile", created_at="2026-05-01T00:00:00+00:00")

    stats = scan_contradictions(db)
    assert stats["invalidated"] == 0  # nothing to do


# ── dry run: no mutations ────────────────────────────────────────────────────

def test_dry_run_no_invalidation(tmp_path):
    from agent.caretaker import scan_contradictions
    db = _init_db(tmp_path)
    _insert(db, id="old", content="User lives in Atlanta", importance=7,
            category="profile", created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="new", content="User lives in Multan", importance=7,
            category="profile", created_at="2026-05-01T00:00:00+00:00")

    stats = scan_contradictions(db, dry_run=True)
    assert stats["conflicts_found"] == 1
    assert stats["invalidated"] == 0  # dry-run flag honoured
    assert _read_invalid(db, "old") is None
    assert _read_invalid(db, "new") is None


# ── full() runs contradiction scan ───────────────────────────────────────────

def test_full_includes_contradiction_scan(tmp_path):
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="old", content="User lives in Atlanta", importance=7,
            category="profile", created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="new", content="User lives in Multan", importance=7,
            category="profile", created_at="2026-05-01T00:00:00+00:00")

    # Stub embeddings so dedup pass doesn't crash
    with patch.object(caretaker, "_get_embedding_safe", return_value=None):
        stats = caretaker.full(db)

    assert stats.get("contradictions_invalidated") == 1


# ── deep mode: Haiku call ────────────────────────────────────────────────────

def test_deep_mode_calls_haiku_per_category(tmp_path):
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="a", content="User lives in Atlanta", importance=7,
            category="profile", created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="b", content="User likes coffee", importance=5,
            category="profile", created_at="2026-02-01T00:00:00+00:00")
    _insert(db, id="c", content="Trip planned", importance=4,
            category="travel", created_at="2026-03-01T00:00:00+00:00")

    calls = []
    def fake_review(category, facts, client=None):
        calls.append((category, len(facts)))
        return f"reviewed {category}"

    with patch.object(caretaker, "_try_haiku_review", side_effect=fake_review):
        stats = caretaker.deep(db)

    assert stats["categories_reviewed"] == 2
    categories_called = {c for c, _ in calls}
    assert categories_called == {"profile", "travel"}


def test_deep_mode_dry_run_skips_haiku(tmp_path):
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="a", content="User likes coffee", importance=5,
            category="profile", created_at="2026-01-01T00:00:00+00:00")

    called = []
    def fake_review(*a, **kw):
        called.append(1)
        return "x"

    with patch.object(caretaker, "_try_haiku_review", side_effect=fake_review):
        stats = caretaker.deep(db, dry_run=True)

    assert called == []  # dry-run skips the LLM call
    assert stats["categories_reviewed"] == 1
