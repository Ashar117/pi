"""testing/test_caretaker_full.py — T-125b: dedup-mode caretaker tests."""
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
    row = conn.execute(
        "SELECT content, importance, superseded_by FROM l3_cache WHERE id = ?",
        (id_,),
    ).fetchone()
    conn.close()
    return row


def _stub_embeddings(mapping):
    """Return a fake embedding function that pulls from a content→vector dict."""
    def _emb(text):
        return mapping.get(text)
    return _emb


def _stub_cosine(a, b):
    """Simple dot product for normalised test vectors."""
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# ── high-cosine pair gets deduped ────────────────────────────────────────────

def test_full_dedup_merges_high_cosine_pair(tmp_path):
    from agent import caretaker
    db = _init_db(tmp_path)

    # Two near-identical facts in the same category
    _insert(db, id="a", content="User likes coffee", importance=7,
            category="profile", created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="b", content="User loves coffee", importance=5,
            category="profile", created_at="2026-01-02T00:00:00+00:00")

    embs = {
        "User likes coffee": [1.0, 0.0],
        "User loves coffee": [0.95, 0.31],  # cosine ~0.95
    }

    with patch.object(caretaker, "_get_embedding_safe", _stub_embeddings(embs)), \
         patch.object(caretaker, "_cosine_safe", _stub_cosine):
        stats = caretaker.full(db)

    assert stats["deduped"] == 1
    # Higher importance "a" should be the winner
    row_a = _read(db, "a")
    row_b = _read(db, "b")
    assert row_a[2] is None  # winner unchanged
    assert row_b[2] == "a"  # loser superseded by "a"


# ── below threshold leaves rows alone ────────────────────────────────────────

def test_full_skips_low_cosine_pair(tmp_path):
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="a", content="User likes coffee", importance=7,
            category="profile", created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="b", content="Weather is sunny today", importance=5,
            category="profile", created_at="2026-01-02T00:00:00+00:00")

    embs = {
        "User likes coffee": [1.0, 0.0],
        "Weather is sunny today": [0.0, 1.0],  # cosine 0
    }

    with patch.object(caretaker, "_get_embedding_safe", _stub_embeddings(embs)), \
         patch.object(caretaker, "_cosine_safe", _stub_cosine):
        stats = caretaker.full(db)

    assert stats["deduped"] == 0
    assert _read(db, "a")[2] is None
    assert _read(db, "b")[2] is None


# ── winner picked by importance ──────────────────────────────────────────────

def test_winner_is_higher_importance(tmp_path):
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="low", content="X", importance=3, category="c",
            created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="hi", content="X (rephrased)", importance=9, category="c",
            created_at="2026-01-02T00:00:00+00:00")

    embs = {"X": [1.0, 0.0], "X (rephrased)": [1.0, 0.0]}  # identical embedding
    with patch.object(caretaker, "_get_embedding_safe", _stub_embeddings(embs)), \
         patch.object(caretaker, "_cosine_safe", _stub_cosine):
        caretaker.full(db)

    assert _read(db, "low")[2] == "hi"
    assert _read(db, "hi")[2] is None


# ── tie-break by newer created_at ────────────────────────────────────────────

def test_winner_tiebreak_by_created_at(tmp_path):
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="old", content="X", importance=5, category="c",
            created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="new", content="X", importance=5, category="c",
            created_at="2026-02-01T00:00:00+00:00")

    embs = {"X": [1.0, 0.0]}
    with patch.object(caretaker, "_get_embedding_safe", _stub_embeddings(embs)), \
         patch.object(caretaker, "_cosine_safe", _stub_cosine):
        caretaker.full(db)

    assert _read(db, "old")[2] == "new"
    assert _read(db, "new")[2] is None


# ── different categories do not merge ────────────────────────────────────────

def test_different_categories_not_merged(tmp_path):
    """Same content, different categories — must NOT merge."""
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="a", content="X", importance=7, category="profile",
            created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="b", content="X", importance=7, category="travel",
            created_at="2026-01-02T00:00:00+00:00")

    embs = {"X": [1.0, 0.0]}
    with patch.object(caretaker, "_get_embedding_safe", _stub_embeddings(embs)), \
         patch.object(caretaker, "_cosine_safe", _stub_cosine):
        stats = caretaker.full(db)

    assert stats["deduped"] == 0
    assert _read(db, "a")[2] is None
    assert _read(db, "b")[2] is None


# ── dry run leaves DB untouched ──────────────────────────────────────────────

def test_dry_run_no_mutations(tmp_path):
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="a", content="X", importance=7, category="c",
            created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="b", content="X (different)", importance=5, category="c",
            created_at="2026-01-02T00:00:00+00:00")

    embs = {"X": [1.0, 0.0], "X (different)": [1.0, 0.0]}
    with patch.object(caretaker, "_get_embedding_safe", _stub_embeddings(embs)), \
         patch.object(caretaker, "_cosine_safe", _stub_cosine):
        stats = caretaker.full(db, dry_run=True)

    # In dry-run mode, deduped should stay 0 and DB unchanged
    assert stats["deduped"] == 0
    assert _read(db, "a")[2] is None
    assert _read(db, "b")[2] is None


# ── already-superseded rows are skipped ──────────────────────────────────────

def test_already_superseded_rows_skipped(tmp_path):
    """A row already marked superseded_by should not be reconsidered."""
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="winner", content="X", importance=8, category="c",
            created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="loser", content="X", importance=4, category="c",
            created_at="2026-01-02T00:00:00+00:00",
            superseded_by="winner")
    _insert(db, id="other", content="X", importance=6, category="c",
            created_at="2026-01-03T00:00:00+00:00")

    embs = {"X": [1.0, 0.0]}
    with patch.object(caretaker, "_get_embedding_safe", _stub_embeddings(embs)), \
         patch.object(caretaker, "_cosine_safe", _stub_cosine):
        caretaker.full(db)

    # winner stays clean; loser stays superseded_by=winner; "other" gets deduped vs winner
    assert _read(db, "winner")[2] is None
    assert _read(db, "loser")[2] == "winner"
    assert _read(db, "other")[2] == "winner"


# ── derived rows excluded from dedup ─────────────────────────────────────────

def test_derived_rows_not_deduped(tmp_path):
    from agent import caretaker
    db = _init_db(tmp_path)
    _insert(db, id="stated", content="User born 2006-08-17", importance=9,
            category="profile", created_at="2026-01-01T00:00:00+00:00")
    _insert(db, id="derived", content="User is 19", importance=8,
            category="profile", created_at="2026-01-01T00:00:00+00:00",
            kind="derived", source_id="stated", formula="age_from_birthday")

    embs = {"User born 2006-08-17": [1.0, 0.0], "User is 19": [1.0, 0.0]}
    with patch.object(caretaker, "_get_embedding_safe", _stub_embeddings(embs)), \
         patch.object(caretaker, "_cosine_safe", _stub_cosine):
        caretaker.full(db)

    # derived not touched
    assert _read(db, "derived")[2] is None
    # stated not touched either (derived was excluded from comparison)
    assert _read(db, "stated")[2] is None


# ── full() runs lite() inside ────────────────────────────────────────────────

def test_full_also_runs_lite(tmp_path):
    """full() should recompute derived facts in addition to deduping."""
    from agent import caretaker
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

    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    # Stub embeddings so dedup pass doesn't crash on real model load
    with patch.object(caretaker, "_get_embedding_safe", return_value=None):
        stats = caretaker.full(db, now=now)

    assert stats["recomputed"] == 1
