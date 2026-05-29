"""testing/test_l3_search_fast_path.py — T-111: L3 fast-path and BM25 cap tests."""
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_memory(tmp_path, supabase_url=""):
    from tools.tools_memory import MemoryTools
    db = tmp_path / "pi.db"
    mt = MemoryTools.__new__(MemoryTools)
    mt.sqlite_path = str(db)
    mt.supabase = None
    mt._sync_lock = threading.Lock()
    mt._supa_lock = threading.RLock()
    mt.namespace = "default"
    mt.supabase_url = supabase_url
    return mt


def _init_db(mt):
    conn = sqlite3.connect(mt.sqlite_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS l3_cache (
            id TEXT PRIMARY KEY,
            content TEXT,
            importance INTEGER,
            category TEXT,
            active_until TEXT,
            invalid_at TEXT,
            created_at TEXT,
            kind TEXT,
            source_id TEXT,
            recompute_after TEXT,
            formula TEXT,
            superseded_by TEXT
        )
    """)
    conn.commit()
    conn.close()


def _insert_row(mt, id_, content, importance=5, invalid_at=None, days_old=0):
    created_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    conn = sqlite3.connect(mt.sqlite_path)
    conn.execute(
        "INSERT INTO l3_cache(id, content, importance, category, active_until, invalid_at, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (id_, content, importance, "note", None, invalid_at, created_at),
    )
    conn.commit()
    conn.close()


# ── Fast path when under threshold ────────────────────────────────────────────

def test_fast_path_when_under_threshold(tmp_path):
    """With < threshold rows, BM25 code is not executed."""
    mt = _make_memory(tmp_path)
    _init_db(mt)
    for i in range(50):
        _insert_row(mt, f"id{i}", f"content hello world {i}", importance=5)

    bm25_called = []

    class _FakeBM25:
        def __init__(self, *a, **kw):
            bm25_called.append(1)

        def get_scores(self, q):
            return [0.0]

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200", "PI_L3_BM25_CAP": "1000"}), \
         patch("tools.tools_memory._BM25Okapi", _FakeBM25), \
         patch("tools.tools_memory._BM25_AVAILABLE", True):
        result = mt._hybrid_search_l3("hello", 10)

    assert len(bm25_called) == 0, "BM25 should not be called when under threshold"
    assert len(result) > 0


def test_bm25_path_when_over_threshold(tmp_path):
    """With >= threshold rows, BM25 is used."""
    mt = _make_memory(tmp_path)
    _init_db(mt)
    for i in range(300):
        _insert_row(mt, f"id{i}", f"content item {i}", importance=5)

    bm25_called = []

    class _FakeBM25:
        def __init__(self, corpus, *a, **kw):
            bm25_called.append(len(corpus))

        def get_scores(self, q):
            return [0.6] * bm25_called[0] if bm25_called else []

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200", "PI_L3_BM25_CAP": "500"}), \
         patch("tools.tools_memory._BM25Okapi", _FakeBM25), \
         patch("tools.tools_memory._BM25_AVAILABLE", True):
        result = mt._hybrid_search_l3("content", 10)

    assert len(bm25_called) == 1, "BM25 should be called for large cache"


def test_bm25_cap_limits_corpus(tmp_path):
    """BM25 corpus is capped at PI_L3_BM25_CAP rows."""
    mt = _make_memory(tmp_path)
    _init_db(mt)
    for i in range(500):
        _insert_row(mt, f"id{i}", f"content {i}", importance=5)

    corpus_sizes = []

    class _FakeBM25:
        def __init__(self, corpus, *a, **kw):
            corpus_sizes.append(len(corpus))

        def get_scores(self, q):
            return [0.0] * (corpus_sizes[-1] if corpus_sizes else 0)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "100", "PI_L3_BM25_CAP": "150"}), \
         patch("tools.tools_memory._BM25Okapi", _FakeBM25), \
         patch("tools.tools_memory._BM25_AVAILABLE", True):
        mt._hybrid_search_l3("content", 10)

    assert corpus_sizes and corpus_sizes[0] <= 150, f"BM25 corpus should be capped at 150, got {corpus_sizes}"


# ── Fast-path score ordering ──────────────────────────────────────────────────

def test_score_ordering_fast_path(tmp_path):
    """Rows with content match rank above non-matches; high-importance above low."""
    mt = _make_memory(tmp_path)
    _init_db(mt)

    # One row with 'hello' in content, high importance
    _insert_row(mt, "match_hi", "hello world is great", importance=9, days_old=0)
    # One row with 'hello', low importance
    _insert_row(mt, "match_lo", "hello planet earth", importance=2, days_old=0)
    # One row without 'hello'
    _insert_row(mt, "nomatch", "goodbye cruel world", importance=8, days_old=0)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200"}), \
         patch("tools.tools_memory._BM25_AVAILABLE", True):
        result = mt._l3_fast_path("hello", 10)

    ids = [r[0] for r in result]
    # Only matching rows returned (T-145: non-matches must be filtered out)
    assert "match_hi" in ids
    assert "match_lo" in ids
    assert "nomatch" not in ids, "non-matching row must not be returned by fast-path"
    # High importance match above low importance match
    assert ids.index("match_hi") < ids.index("match_lo")


# ── Threshold env override ────────────────────────────────────────────────────

def test_threshold_env_override(tmp_path):
    """PI_L3_FAST_PATH_THRESHOLD=10 forces BM25 even for 15 rows."""
    mt = _make_memory(tmp_path)
    _init_db(mt)
    for i in range(15):
        _insert_row(mt, f"id{i}", f"content {i}", importance=5)

    bm25_called = []

    class _FakeBM25:
        def __init__(self, corpus, *a, **kw):
            bm25_called.append(len(corpus))

        def get_scores(self, q):
            return [0.0] * (bm25_called[-1] if bm25_called else 0)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "10", "PI_L3_BM25_CAP": "1000"}), \
         patch("tools.tools_memory._BM25Okapi", _FakeBM25), \
         patch("tools.tools_memory._BM25_AVAILABLE", True):
        mt._hybrid_search_l3("content", 5)

    assert len(bm25_called) == 1, "BM25 should be used when threshold is set low"


# ── Invalid env falls back ────────────────────────────────────────────────────

def test_invalid_env_falls_back(tmp_path):
    """PI_L3_BM25_CAP=foo falls back to default; track_silent recorded."""
    mt = _make_memory(tmp_path)

    recorded = []

    def fake_track(cat, exc=None, **kw):
        recorded.append(cat)

    with patch("tools.tools_memory.MemoryTools._l3_env_int",
               wraps=mt._l3_env_int) as mock_env:
        with patch.dict(os.environ, {"PI_L3_BM25_CAP": "foo"}), \
             patch("agent.observability.track_silent", fake_track):
            val = mt._l3_env_int("PI_L3_BM25_CAP", 1000)

    assert val == 1000
    assert "config.invalid_env" in recorded
