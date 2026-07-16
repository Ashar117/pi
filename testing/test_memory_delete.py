"""testing/test_memory_delete.py — T-139/T-140/T-144/T-145: memory_delete safety guards."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── fixture helpers ────────────────────────────────────────────────────────────

def _make_tools(tmp_path):
    from tools.tools_memory import MemoryTools
    db = tmp_path / "pi.db"
    mt = MemoryTools.__new__(MemoryTools)
    mt.sqlite_path = str(db)
    mt.supabase = MagicMock()
    mt.supabase.table.return_value.update.return_value.in_.return_value.execute.return_value = None
    mt.supabase.table.return_value.delete.return_value.in_.return_value.execute.return_value = None
    mt._sync_lock = threading.Lock()
    mt._supa_lock = threading.RLock()
    mt._last_sync = datetime.now(timezone.utc)
    mt._sync_ttl_seconds = 9999
    mt.namespace = "default"
    mt.supabase_url = ""
    return mt


def _init_db(mt, rows: list):
    conn = sqlite3.connect(mt.sqlite_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS l3_cache (
            id TEXT PRIMARY KEY,
            content TEXT,
            importance INTEGER DEFAULT 5,
            category TEXT DEFAULT 'note',
            active_until TEXT,
            invalid_at TEXT,
            created_at TEXT,
            superseded_by TEXT
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO l3_cache (id, content, importance, category, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [r["id"], r["content"], r.get("importance", 5),
             r.get("category", "note"), "2026-01-01T00:00:00Z"],
        )
    conn.commit()
    conn.close()


def _active_ids(mt):
    conn = sqlite3.connect(mt.sqlite_path)
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM l3_cache WHERE invalid_at IS NULL"
    ).fetchall()]
    conn.close()
    return set(ids)


def _make_rows(n=20):
    return [
        {"id": str(uuid.uuid4()), "content": f"unique fact {uuid.uuid4().hex} number {i}"}
        for i in range(n)
    ]


# ── T-139: ID-based delete targets only that row ─────────────────────────────

def test_id_prefix_deletes_only_target(tmp_path):
    mt = _make_tools(tmp_path)
    rows = _make_rows(5)
    _init_db(mt, rows)
    target_id = rows[2]["id"]

    result = mt.memory_delete(target_id, soft=True)

    assert result.get("deleted") == 1
    assert result["entries"] == [target_id]
    active = _active_ids(mt)
    assert target_id not in active
    assert len(active) == 4  # only the target was soft-deleted


def test_id_prefix_short_prefix(tmp_path):
    mt = _make_tools(tmp_path)
    rows = [
        {"id": "aaaa1111-0000-0000-0000-000000000000", "content": "profile fact"},
        {"id": "bbbb2222-0000-0000-0000-000000000000", "content": "other fact"},
    ]
    _init_db(mt, rows)

    result = mt.memory_delete("aaaa1111", soft=True)
    assert result.get("deleted") == 1
    active = _active_ids(mt)
    assert "aaaa1111-0000-0000-0000-000000000000" not in active
    assert "bbbb2222-0000-0000-0000-000000000000" in active


# ── T-140/T-145: bulk guard fires when >3 entries would be deleted ────────────

def test_bulk_guard_fires_without_force(tmp_path):
    mt = _make_tools(tmp_path)
    # 20 rows, all with same distinctive word
    word = uuid.uuid4().hex
    rows = [
        {"id": f"row-{i:02d}-{uuid.uuid4().hex[:6]}", "content": f"{word} entry {i}"}
        for i in range(20)
    ]
    _init_db(mt, rows)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200"}):
        result = mt.memory_delete(word)

    assert "error" in result
    assert result["would_delete"] > 3
    # Nothing should have been deleted
    assert len(_active_ids(mt)) == 20


def test_bulk_guard_allows_with_force(tmp_path):
    mt = _make_tools(tmp_path)
    word = uuid.uuid4().hex
    rows = [
        {"id": f"row-{i:02d}-{uuid.uuid4().hex[:6]}", "content": f"{word} entry {i}"}
        for i in range(5)
    ]
    _init_db(mt, rows)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200"}):
        result = mt.memory_delete(word, force=True)

    assert result.get("deleted") == 5
    assert len(_active_ids(mt)) == 0


def test_bulk_guard_not_triggered_for_single(tmp_path):
    mt = _make_tools(tmp_path)
    unique = uuid.uuid4().hex
    rows = [
        {"id": f"target-{uuid.uuid4().hex[:8]}", "content": f"the {unique} fact"},
        {"id": f"other-{uuid.uuid4().hex[:8]}", "content": "completely different"},
    ]
    _init_db(mt, rows)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200"}):
        result = mt.memory_delete(unique)

    assert result.get("deleted") == 1
    assert len(_active_ids(mt)) == 1


# ── T-139/T-145: non-matching query deletes nothing ──────────────────────────

def test_nonmatch_query_deletes_nothing(tmp_path):
    mt = _make_tools(tmp_path)
    rows = _make_rows(10)
    _init_db(mt, rows)

    before = len(_active_ids(mt))
    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200"}):
        result = mt.memory_delete("zzzzz_no_such_content_xyzzy")

    assert result.get("deleted") == 0
    assert len(_active_ids(mt)) == before, "non-matching delete must not touch any rows"


# ── soft-delete sets invalid_at, not hard DELETE ──────────────────────────────

def test_soft_delete_sets_invalid_at(tmp_path):
    mt = _make_tools(tmp_path)
    rid = str(uuid.uuid4())
    rows = [{"id": rid, "content": f"important {uuid.uuid4().hex} profile"}]
    _init_db(mt, rows)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200"}):
        mt.memory_delete(rid, soft=True)

    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute("SELECT invalid_at FROM l3_cache WHERE id=?", [rid]).fetchone()
    conn.close()
    assert row is not None, "row must still exist (not hard-deleted)"
    assert row[0] is not None, "invalid_at must be set for soft-delete"


def test_hard_delete_removes_row(tmp_path):
    mt = _make_tools(tmp_path)
    rid = str(uuid.uuid4())
    rows = [{"id": rid, "content": f"stale {uuid.uuid4().hex} data"}]
    _init_db(mt, rows)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200"}):
        result = mt.memory_delete(rid, soft=False)

    assert result.get("deleted") == 1
    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute("SELECT id FROM l3_cache WHERE id=?", [rid]).fetchone()
    conn.close()
    assert row is None, "hard-delete must remove the row"


# ── WAL snapshot written before deletion ─────────────────────────────────────

def test_wal_snapshot_written(tmp_path):
    mt = _make_tools(tmp_path)
    rid = str(uuid.uuid4())
    rows = [{"id": rid, "content": f"wal test {uuid.uuid4().hex}"}]
    _init_db(mt, rows)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200"}):
        mt.memory_delete(rid, soft=True)

    wal_path = os.path.join(os.path.dirname(mt.sqlite_path), "delete_wal.jsonl")
    assert os.path.exists(wal_path), "WAL file must be created"
    with open(wal_path, encoding="utf-8") as f:
        entry = json.loads(f.readline())
    assert entry["target"] == rid
    assert any(e["id"] == rid for e in entry["entries"])


# ── empty target returns error dict ──────────────────────────────────────────

def test_empty_target_returns_error(tmp_path):
    mt = _make_tools(tmp_path)
    _init_db(mt, _make_rows(3))

    result = mt.memory_delete("")
    assert "error" in result
    assert result["deleted"] == 0


# ── fast-path non-match filter (T-145 root cause) ─────────────────────────────

def test_fast_path_does_not_return_nonmatching_rows(tmp_path):
    """_l3_fast_path must not return rows when query doesn't appear in content."""
    from tools.tools_memory import MemoryTools
    mt = _make_tools(tmp_path)
    rows = _make_rows(10)
    _init_db(mt, rows)

    with patch.dict(os.environ, {"PI_L3_FAST_PATH_THRESHOLD": "200"}):
        results = mt._l3_fast_path("zzz_never_in_content_xyzzy_abc", 10)

    assert results == [], "fast-path must return empty list when query matches nothing"


# ── T-302: semantic forget ────────────────────────────────────────────────────
# Uses the real constructor (not the bare _make_tools fixture) so _init_sqlite
# creates the full schema (embedding column included) — matches the pattern in
# test_hybrid_retriever.py / test_l3_embeddings.py.

def _offline_mt(tmp_path):
    from tools.tools_memory import MemoryTools
    return MemoryTools(supabase_url="", supabase_key="",
                        sqlite_path=str(tmp_path / "pi.db"))


def test_semantic_forget_finds_paraphrase_lexical_alone_misses(tmp_path):
    """The proof: 'my old internship' has zero lexical overlap with the stored
    fact, but semantic (dense cosine) forget finds and invalidates it."""
    mt = _offline_mt(tmp_path)
    mt.memory_write(content="started the summer role at Meta in June",
                     tier="l3", category="note", importance=7)

    with patch("memory.semantic_dedup.compute_embedding_for_write", return_value=[1.0, 0.0]):
        mt.backfill_l3_embeddings(limit=10)

    # Ground the gap: plain lexical lookup finds nothing.
    lexical_only = mt.memory_read("my old internship", tier="l3")
    assert lexical_only == [], "test setup invalid: query must not lexically match"

    with patch("memory.semantic_dedup.get_embedding", return_value=[1.0, 0.0]):
        result = mt.memory_delete("my old internship")

    assert result["deleted"] == 1, f"semantic forget should find the paraphrased fact: {result}"
    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute(
        "SELECT invalid_at FROM l3_cache WHERE content LIKE '%Meta%'"
    ).fetchone()
    conn.close()
    assert row[0] is not None, "matched row must be soft-invalidated, not left active"


def test_semantic_forget_still_soft_by_default(tmp_path):
    mt = _offline_mt(tmp_path)
    mt.memory_write(content="started the summer role at Meta in June",
                     tier="l3", category="note", importance=7)
    with patch("memory.semantic_dedup.compute_embedding_for_write", return_value=[1.0, 0.0]):
        mt.backfill_l3_embeddings(limit=10)

    with patch("memory.semantic_dedup.get_embedding", return_value=[1.0, 0.0]):
        mt.memory_delete("my old internship")  # soft=True default

    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute("SELECT id, invalid_at FROM l3_cache WHERE content LIKE '%Meta%'").fetchone()
    conn.close()
    assert row is not None, "row must still exist — semantic forget must not hard-delete by default"
    assert row[1] is not None


def test_semantic_forget_generic_query_does_not_sweep_everything(tmp_path):
    """Risk-note guard: a generic query ('stuff') must not match unrelated memories."""
    mt = _offline_mt(tmp_path)
    mt.memory_write(content="the lab uses zebrafish as the model organism",
                     tier="l3", category="note", importance=8)
    mt.memory_write(content="my sister lives in Boston",
                     tier="l3", category="note", importance=8)
    with patch("memory.semantic_dedup.compute_embedding_for_write", return_value=[1.0, 0.0]):
        mt.backfill_l3_embeddings(limit=10)

    with patch("memory.semantic_dedup.get_embedding", return_value=[0.0, 1.0]):  # orthogonal
        result = mt.memory_delete("stuff")

    assert result["deleted"] == 0, f"generic query must not sweep unrelated memories: {result}"


def test_semantic_forget_bulk_guard_still_fires(tmp_path):
    """>3 semantic matches must still require force=True."""
    mt = _offline_mt(tmp_path)
    facts = [
        "the zebrafish tank filter needs a clean",
        "ordered new food pellets for the aquarium",
        "the water pH in the tank reads 7.2 today",
        "scheduled a vet check for the fish enclosure",
        "the aquarium heater was replaced last week",
    ]
    for f in facts:
        mt.memory_write(content=f, tier="l3", category="note", importance=5)
    with patch("memory.semantic_dedup.compute_embedding_for_write", return_value=[1.0, 0.0]):
        mt.backfill_l3_embeddings(limit=10)

    with patch("memory.semantic_dedup.get_embedding", return_value=[1.0, 0.0]):
        result = mt.memory_delete("aquatic experiment maintenance")

    assert "error" in result, f"bulk guard should fire for >3 semantic matches: {result}"
    assert result["would_delete"] >= 4
