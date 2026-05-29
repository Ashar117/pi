"""testing/test_recall.py — T-123: tagged/reply-to recall tests."""
import os
import sqlite3
import sys
import uuid
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


# ── empty input returns empty ────────────────────────────────────────────────

def test_recall_empty_text_returns_empty(tmp_path):
    from memory.recall import recall_referenced
    db = _init_db(tmp_path)
    assert recall_referenced("", db_path=db) == []
    assert recall_referenced("   ", db_path=db) == []


# ── no embedding available → empty ───────────────────────────────────────────

def test_recall_no_embedding_returns_empty(tmp_path):
    from memory import recall as recall_mod
    db = _init_db(tmp_path)
    _insert(db, id="x", content="some fact", importance=5, category="note", created_at="2026-01-01")

    with patch.object(recall_mod, "_get_embedding", return_value=None):
        result = recall_mod.recall_referenced("looking for fact", db_path=db)
    assert result == []


# ── hit returns canonical entry ──────────────────────────────────────────────

def test_recall_hit_returns_canonical_entry(tmp_path):
    from memory import recall as recall_mod
    db = _init_db(tmp_path)
    _insert(db, id="match", content="User likes oat milk",
            importance=7, category="profile", created_at="2026-01-01")
    _insert(db, id="other", content="The weather is nice today",
            importance=3, category="note", created_at="2026-01-02")

    def fake_embed(text):
        # Return high cosine for the match content, low for others
        if "oat" in text.lower():
            return [1.0, 0.0]
        return [0.0, 1.0]

    def fake_cos(a, b):
        # dot product for normalised vectors
        return sum(x*y for x, y in zip(a, b))

    with patch.object(recall_mod, "_get_embedding", fake_embed), \
         patch.object(recall_mod, "_cosine", fake_cos):
        result = recall_mod.recall_referenced("the user's preferred oat milk", db_path=db, threshold=0.5)

    assert len(result) == 1
    assert result[0]["id"] == "match"
    assert result[0]["score"] > 0.9


# ── miss below threshold returns empty ──────────────────────────────────────

def test_recall_below_threshold_returns_empty(tmp_path):
    from memory import recall as recall_mod
    db = _init_db(tmp_path)
    _insert(db, id="some", content="totally unrelated content",
            importance=5, category="note", created_at="2026-01-01")

    with patch.object(recall_mod, "_get_embedding", return_value=[1.0, 0.0]), \
         patch.object(recall_mod, "_cosine", return_value=0.2):  # below 0.85
        result = recall_mod.recall_referenced("anything", db_path=db)
    assert result == []


# ── format_recall_context ────────────────────────────────────────────────────

def test_format_recall_context_empty():
    from memory.recall import format_recall_context
    assert format_recall_context([]) == ""


def test_format_recall_context_renders_hits():
    from memory.recall import format_recall_context
    hits = [
        {"id": "a", "content": "User likes coffee", "importance": 8,
         "category": "profile", "created_at": "2026-05-01T00:00:00", "score": 0.91},
    ]
    out = format_recall_context(hits)
    assert "RECALLED CONTEXT" in out
    assert "User likes coffee" in out
    assert "2026-05-01" in out


# ── invalidated entries excluded ─────────────────────────────────────────────

def test_recall_skips_invalidated_entries(tmp_path):
    from memory import recall as recall_mod
    db = _init_db(tmp_path)
    _insert(db, id="invalid", content="old fact superseded",
            importance=7, category="profile", created_at="2025-01-01",
            invalid_at="2026-01-01T00:00:00")

    with patch.object(recall_mod, "_get_embedding", return_value=[1.0, 0.0]), \
         patch.object(recall_mod, "_cosine", return_value=0.99):
        result = recall_mod.recall_referenced("the old fact", db_path=db)
    assert result == []  # invalidated row not even considered


# ── derived pending placeholders excluded ────────────────────────────────────

def test_recall_skips_derived_pending_placeholders(tmp_path):
    from memory import recall as recall_mod
    db = _init_db(tmp_path)
    _insert(db, id="pending", content="(pending recompute from xyz)",
            importance=8, category="derived", created_at="2026-01-01",
            kind="derived", source_id="xyz", formula="age_from_birthday")

    with patch.object(recall_mod, "_get_embedding", return_value=[1.0, 0.0]), \
         patch.object(recall_mod, "_cosine", return_value=0.99):
        result = recall_mod.recall_referenced("anything", db_path=db)
    assert result == []
