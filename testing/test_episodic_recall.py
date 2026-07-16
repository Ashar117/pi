"""Tests for T-205: episodic recall — close_conversation + recall_episode."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_memory(tmp_path: Path):
    from tools.tools_memory import MemoryTools
    mem = MemoryTools.__new__(MemoryTools)
    mem.sqlite_path = str(tmp_path / "pi.db")
    mem.supabase = MagicMock()
    mem._init_sqlite()
    return mem


# ── Schema ────────────────────────────────────────────────────────────────────

def test_digest_column_exists(tmp_path):
    import sqlite3
    mem = _make_memory(tmp_path)
    conn = sqlite3.connect(mem.sqlite_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    conn.close()
    assert "digest" in cols


def test_digest_migration_idempotent(tmp_path):
    """Calling _init_sqlite twice does not raise even if digest column already exists."""
    mem = _make_memory(tmp_path)
    mem._init_sqlite()  # second call — should not raise


# ── close_conversation ────────────────────────────────────────────────────────

def test_close_conversation_writes_digest(tmp_path):
    import sqlite3
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.close_conversation("cv1", "We decided to use FastAPI for the brain server.")

    conn = sqlite3.connect(mem.sqlite_path)
    row = conn.execute("SELECT digest FROM conversations WHERE id='cv1'").fetchone()
    conn.close()
    assert row[0] == "We decided to use FastAPI for the brain server."


def test_close_conversation_overwrites(tmp_path):
    import sqlite3
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.close_conversation("cv1", "First digest.")
    mem.close_conversation("cv1", "Updated digest.")

    conn = sqlite3.connect(mem.sqlite_path)
    row = conn.execute("SELECT digest FROM conversations WHERE id='cv1'").fetchone()
    conn.close()
    assert row[0] == "Updated digest."


def test_close_conversation_truncates_to_400(tmp_path):
    import sqlite3
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    long_digest = "x" * 500
    mem.close_conversation("cv1", long_digest)

    conn = sqlite3.connect(mem.sqlite_path)
    row = conn.execute("SELECT digest FROM conversations WHERE id='cv1'").fetchone()
    conn.close()
    assert len(row[0]) == 400


# ── recall_episode ────────────────────────────────────────────────────────────

def test_recall_episode_returns_matching_conversation(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.close_conversation("cv1", "We decided to migrate the memory layer to Postgres.")
    mem.persist_turn("cv1", "user", "hi", idx=0, ts="2026-06-01T00:01:00+00:00")

    results = mem.recall_episode("memory migration Postgres")
    assert len(results) == 1
    assert results[0]["id"] == "cv1"
    assert "Postgres" in results[0]["digest"]


def test_recall_episode_empty_when_no_digests(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    # No digest written

    results = mem.recall_episode("anything")
    assert results == []


def test_recall_episode_filters_by_relevance(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.close_conversation("cv1", "Discussed neural network training and loss functions.")
    mem.create_conversation("cv2", "root", "2026-06-02T00:00:00+00:00")
    mem.close_conversation("cv2", "Discussed FastAPI routes and auth tokens.")
    mem.persist_turn("cv2", "user", "hi", idx=0, ts="2026-06-02T00:01:00+00:00")
    mem.persist_turn("cv1", "user", "hi", idx=0, ts="2026-06-01T00:01:00+00:00")

    results = mem.recall_episode("neural network training", limit=4)
    assert len(results) >= 1
    assert results[0]["id"] == "cv1"


def test_recall_episode_returns_dict_with_expected_keys(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.close_conversation("cv1", "Test digest.")

    results = mem.recall_episode("test")
    assert len(results) == 1
    ep = results[0]
    assert {"id", "title", "mode", "created_at", "digest"} <= ep.keys()


def test_recall_episode_limit_respected(tmp_path):
    mem = _make_memory(tmp_path)
    for i in range(6):
        cid = f"cv{i}"
        mem.create_conversation(cid, "root", f"2026-06-0{i+1}T00:00:00+00:00")
        mem.close_conversation(cid, f"Conversation {i} about testing and code.")

    results = mem.recall_episode("testing code", limit=3)
    assert len(results) <= 3


# ── _handle_recall_episode ────────────────────────────────────────────────────

def test_handle_recall_episode_returns_episodes_key(tmp_path):
    from tools.tools_memory import _handle_recall_episode
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.close_conversation("cv1", "We decided to refactor the memory layer.")

    agent = MagicMock()
    agent.memory = mem
    result = _handle_recall_episode(agent, {"query": "refactor memory"})
    assert "episodes" in result


def test_handle_recall_episode_no_match_returns_message(tmp_path):
    from tools.tools_memory import _handle_recall_episode
    mem = _make_memory(tmp_path)

    agent = MagicMock()
    agent.memory = mem
    result = _handle_recall_episode(agent, {"query": "completely unrelated"})
    assert "message" in result
    assert result["episodes"] == []


# ── ToolSpec in TOOLS list ────────────────────────────────────────────────────

def test_recall_episode_toolspec_in_tools():
    from tools.tools_memory import TOOLS
    names = [t.name for t in TOOLS]
    assert "recall_episode" in names


def test_recall_episode_toolspec_has_required_query():
    from tools.tools_memory import TOOLS
    spec = next(t for t in TOOLS if t.name == "recall_episode")
    assert "query" in spec.input_schema["required"]
