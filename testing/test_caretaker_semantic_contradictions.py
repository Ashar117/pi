"""testing/test_caretaker_semantic_contradictions.py — T-303: LLM-adjudicated
implication-level contradiction pass. Router is mocked — no network calls.
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.caretaker import adjudicate_contradiction, scan_semantic_contradictions  # noqa: E402


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
            superseded_by TEXT,
            embedding TEXT
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


def _fake_router(verdict_text: str):
    router = MagicMock()
    resp = MagicMock()
    resp.text = verdict_text
    router.chat.return_value = resp
    return router


# ── adjudicate_contradiction ──────────────────────────────────────────────────

def test_adjudicate_returns_true_on_contradicts():
    router = _fake_router("CONTRADICTS")
    assert adjudicate_contradiction("moved to Boston", "apartment in Atlanta", router) is True
    call = router.chat.call_args.kwargs
    assert call["tier"] == "cheap"


def test_adjudicate_returns_false_on_compatible():
    router = _fake_router("COMPATIBLE")
    assert adjudicate_contradiction("likes coffee", "likes tea", router) is False


def test_adjudicate_returns_none_on_unparseable_text():
    router = _fake_router("I'm not sure, could go either way")
    assert adjudicate_contradiction("a", "b", router) is None


def test_adjudicate_returns_none_when_router_is_none():
    assert adjudicate_contradiction("a", "b", None) is None


def test_adjudicate_returns_none_when_router_raises():
    router = MagicMock()
    router.chat.side_effect = RuntimeError("all providers failed")
    assert adjudicate_contradiction("a", "b", router) is None


# ── scan_semantic_contradictions ──────────────────────────────────────────────

_EMB_A = [1.0, 0.0]
_EMB_B = [0.99, 0.01]  # cosine ~1.0 with A — close but different topic keys


def test_finds_and_invalidates_implication_contradiction(tmp_path):
    """The Boston/Atlanta case: different topic keys, close embeddings,
    CONTRADICTS verdict -> the OLDER row gets invalidated."""
    db = _init_db(tmp_path)
    now = datetime.now(timezone.utc)
    old_iso = (now - timedelta(days=2)).isoformat()
    new_iso = now.isoformat()

    _insert(db, id="old-atlanta", content="my apartment in Atlanta",
            importance=6, category="note", created_at=old_iso, embedding='[1.0, 0.0]')
    _insert(db, id="new-boston", content="I moved to Boston last month",
            importance=6, category="note", created_at=new_iso, embedding='[0.99, 0.01]')

    router = _fake_router("CONTRADICTS")
    stats = scan_semantic_contradictions(db, router, now=now)

    assert stats["invalidated"] == 1
    assert stats["calls_made"] >= 1
    assert _read_invalid(db, "old-atlanta") is not None, "older row should be invalidated"
    assert _read_invalid(db, "new-boston") is None, "newer row (winner) stays active"


def test_compatible_verdict_keeps_both(tmp_path):
    db = _init_db(tmp_path)
    now = datetime.now(timezone.utc)
    _insert(db, id="a", content="likes coffee in the morning", importance=5,
            category="note", created_at=(now - timedelta(days=1)).isoformat(),
            embedding='[1.0, 0.0]')
    _insert(db, id="b", content="enjoys tea in the evening", importance=5,
            category="note", created_at=now.isoformat(), embedding='[0.99, 0.01]')

    router = _fake_router("COMPATIBLE")
    stats = scan_semantic_contradictions(db, router, now=now)

    assert stats["invalidated"] == 0
    assert _read_invalid(db, "a") is None
    assert _read_invalid(db, "b") is None


def test_router_none_skips_pass_zero_mutations(tmp_path):
    db = _init_db(tmp_path)
    now = datetime.now(timezone.utc)
    _insert(db, id="old-atlanta", content="my apartment in Atlanta", importance=6,
            category="note", created_at=(now - timedelta(days=2)).isoformat(),
            embedding='[1.0, 0.0]')
    _insert(db, id="new-boston", content="I moved to Boston last month", importance=6,
            category="note", created_at=now.isoformat(), embedding='[0.99, 0.01]')

    stats = scan_semantic_contradictions(db, None, now=now)

    assert stats == {"pairs_considered": 0, "calls_made": 0, "invalidated": 0, "dry_run": False}
    assert _read_invalid(db, "old-atlanta") is None
    assert _read_invalid(db, "new-boston") is None


def test_call_cap_respected_with_many_candidates(tmp_path):
    """10 mutually-close candidate pairs, cap=3 — only 3 adjudication calls fire."""
    db = _init_db(tmp_path)
    now = datetime.now(timezone.utc)
    # 5 distinct known relation-phrases -> 5 distinct topic keys, so none of the
    # C(5,2)=10 pairs get excluded by the same-topic-key guard.
    phrases = [
        "user lives in a small town",
        "user works at a startup",
        "user studies at a university",
        "user is based in a coastal city",
        "user was born in a rural area",
    ]
    for i, content in enumerate(phrases):
        _insert(
            db, id=f"row-{i}", content=content,
            importance=5, category="note",
            created_at=(now - timedelta(hours=i)).isoformat(),
            embedding="[1.0, 0.0]",
        )

    router = _fake_router("COMPATIBLE")  # verdict doesn't matter here — counting calls
    stats = scan_semantic_contradictions(db, router, now=now, max_calls=3)

    assert stats["pairs_considered"] == 10  # C(5,2)
    assert stats["calls_made"] == 3
    assert router.chat.call_count == 3


def test_same_topic_key_pairs_are_excluded():
    """Pairs sharing a topic key are the lexical scan's job, not this pass's."""
    from agent.caretaker import _topic_key
    assert _topic_key("User lives in Atlanta") == _topic_key("User lives in Boston")


def test_dry_run_makes_no_mutations(tmp_path):
    db = _init_db(tmp_path)
    now = datetime.now(timezone.utc)
    _insert(db, id="old-atlanta", content="my apartment in Atlanta", importance=6,
            category="note", created_at=(now - timedelta(days=2)).isoformat(),
            embedding='[1.0, 0.0]')
    _insert(db, id="new-boston", content="I moved to Boston last month", importance=6,
            category="note", created_at=now.isoformat(), embedding='[0.99, 0.01]')

    router = _fake_router("CONTRADICTS")
    stats = scan_semantic_contradictions(db, router, dry_run=True, now=now)

    assert stats["dry_run"] is True
    assert stats["invalidated"] == 0
    assert _read_invalid(db, "old-atlanta") is None


def test_missing_db_returns_zero_stats(tmp_path):
    router = _fake_router("CONTRADICTS")
    stats = scan_semantic_contradictions(tmp_path / "nonexistent.db", router)
    assert stats == {"pairs_considered": 0, "calls_made": 0, "invalidated": 0, "dry_run": False}


def test_pre_t291_schema_without_embedding_column_returns_zero_stats(tmp_path):
    """Graceful degrade on a DB created before T-291 added the embedding column."""
    db = tmp_path / "pi.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE l3_cache (
            id TEXT PRIMARY KEY, content TEXT, importance INTEGER, category TEXT,
            active_until TEXT, created_at TEXT, invalid_at TEXT, superseded_by TEXT
        )
    """)
    conn.commit()
    conn.close()

    router = _fake_router("CONTRADICTS")
    stats = scan_semantic_contradictions(db, router)
    assert stats["invalidated"] == 0


# ── full() wiring ─────────────────────────────────────────────────────────────

def test_full_skips_semantic_pass_when_router_none(tmp_path):
    from agent.caretaker import full
    db = _init_db(tmp_path)
    result = full(db, router=None)
    assert result["semantic_contradictions_invalidated"] == 0
    assert result["semantic_contradictions_considered"] == 0


def test_full_runs_semantic_pass_when_router_present(tmp_path):
    from agent.caretaker import full
    db = _init_db(tmp_path)
    now = datetime.now(timezone.utc)
    _insert(db, id="old-atlanta", content="my apartment in Atlanta", importance=6,
            category="note", created_at=(now - timedelta(days=2)).isoformat(),
            embedding='[1.0, 0.0]')
    _insert(db, id="new-boston", content="I moved to Boston last month", importance=6,
            category="note", created_at=now.isoformat(), embedding='[0.99, 0.01]')

    router = _fake_router("CONTRADICTS")
    with patch("agent.caretaker._get_embedding_safe", return_value=None):
        result = full(db, now=now, router=router)

    assert result["semantic_contradictions_invalidated"] == 1
    assert result["applied"] is True
