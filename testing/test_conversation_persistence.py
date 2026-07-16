"""Tests for T-186: multi-conversation persistence (write→resume round-trip)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Minimal MemoryTools setup ─────────────────────────────────────────────────

def _make_memory(tmp_path: Path):
    """Create a MemoryTools instance backed by a temp SQLite DB."""
    from tools.tools_memory import MemoryTools
    mem = MemoryTools.__new__(MemoryTools)
    mem.sqlite_path = str(tmp_path / "pi.db")
    mem.supabase = MagicMock()
    mem._init_sqlite()
    return mem


# ── Schema: tables exist ──────────────────────────────────────────────────────

def test_conversations_table_created(tmp_path):
    mem = _make_memory(tmp_path)
    conn = sqlite3.connect(mem.sqlite_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "conversations" in tables
    assert "conversation_turns" in tables


def test_conversations_table_columns(tmp_path):
    mem = _make_memory(tmp_path)
    conn = sqlite3.connect(mem.sqlite_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    conn.close()
    assert {"id", "title", "mode", "created_at", "last_active_at"} <= cols


def test_conversation_turns_table_columns(tmp_path):
    mem = _make_memory(tmp_path)
    conn = sqlite3.connect(mem.sqlite_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversation_turns)").fetchall()}
    conn.close()
    assert {"id", "conversation_id", "idx", "role", "content_json", "ts"} <= cols


# ── create_conversation ───────────────────────────────────────────────────────

def test_create_conversation_idempotent(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("abc123", "root", "2026-06-01T00:00:00+00:00")
    mem.create_conversation("abc123", "root", "2026-06-01T00:00:00+00:00")  # duplicate
    conn = sqlite3.connect(mem.sqlite_path)
    count = conn.execute("SELECT COUNT(*) FROM conversations WHERE id='abc123'").fetchone()[0]
    conn.close()
    assert count == 1


def test_create_conversation_stores_mode(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "normie", "2026-06-01T00:00:00+00:00")
    conn = sqlite3.connect(mem.sqlite_path)
    row = conn.execute("SELECT mode FROM conversations WHERE id='cv1'").fetchone()
    conn.close()
    assert row[0] == "normie"


# ── persist_turn ──────────────────────────────────────────────────────────────

def test_persist_turn_stores_user_message(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.persist_turn("cv1", "user", "hello world", idx=0, ts="2026-06-01T00:01:00+00:00")

    conn = sqlite3.connect(mem.sqlite_path)
    row = conn.execute(
        "SELECT role, content_json FROM conversation_turns WHERE conversation_id='cv1'"
    ).fetchone()
    conn.close()
    assert row[0] == "user"
    assert json.loads(row[1]) == "hello world"


def test_persist_turn_updates_last_active(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-01-01T00:00:00+00:00")
    mem.persist_turn("cv1", "user", "hi", idx=0, ts="2026-06-15T12:00:00+00:00")

    conn = sqlite3.connect(mem.sqlite_path)
    row = conn.execute("SELECT last_active_at FROM conversations WHERE id='cv1'").fetchone()
    conn.close()
    assert row[0] == "2026-06-15T12:00:00+00:00"


def test_persist_turn_idempotent_on_same_idx(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.persist_turn("cv1", "user", "first", idx=0, ts="2026-06-01T00:01:00+00:00")
    mem.persist_turn("cv1", "user", "duplicate", idx=0, ts="2026-06-01T00:02:00+00:00")

    conn = sqlite3.connect(mem.sqlite_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM conversation_turns WHERE conversation_id='cv1' AND idx=0"
    ).fetchone()[0]
    conn.close()
    assert count == 1  # INSERT OR IGNORE — second write ignored


# ── load_conversation_turns ───────────────────────────────────────────────────

def test_load_turns_round_trip(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.persist_turn("cv1", "user", "what is pi?", idx=0, ts="2026-06-01T00:01:00+00:00")
    mem.persist_turn("cv1", "assistant", "Pi is 3.14159…", idx=1, ts="2026-06-01T00:01:01+00:00")

    turns = mem.load_conversation_turns("cv1")
    assert len(turns) == 2
    assert turns[0] == {"role": "user", "content": "what is pi?"}
    assert turns[1] == {"role": "assistant", "content": "Pi is 3.14159…"}


def test_load_turns_empty_for_unknown_id(tmp_path):
    mem = _make_memory(tmp_path)
    turns = mem.load_conversation_turns("nonexistent")
    assert turns == []


def test_load_turns_ordered_by_idx(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    # Insert out of idx order to verify ORDER BY idx
    mem.persist_turn("cv1", "assistant", "reply", idx=1, ts="2026-06-01T00:01:01+00:00")
    mem.persist_turn("cv1", "user", "question", idx=0, ts="2026-06-01T00:01:00+00:00")

    turns = mem.load_conversation_turns("cv1")
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "assistant"


# ── list_conversations ────────────────────────────────────────────────────────

def test_list_conversations_returns_recent_first(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("old", "root", "2026-01-01T00:00:00+00:00")
    mem.create_conversation("new", "normie", "2026-06-01T00:00:00+00:00")
    mem.persist_turn("new", "user", "hi", idx=0, ts="2026-06-15T00:00:00+00:00")

    convs = mem.list_conversations(limit=10)
    assert convs[0]["id"] == "new"


def test_list_conversations_includes_untitled(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")

    convs = mem.list_conversations()
    assert convs[0]["title"] == "(untitled)"


# ── title_conversation ────────────────────────────────────────────────────────

def test_title_conversation_sets_title(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.title_conversation("cv1", "My test conversation")

    conn = sqlite3.connect(mem.sqlite_path)
    row = conn.execute("SELECT title FROM conversations WHERE id='cv1'").fetchone()
    conn.close()
    assert row[0] == "My test conversation"


def test_title_conversation_does_not_overwrite(tmp_path):
    mem = _make_memory(tmp_path)
    mem.create_conversation("cv1", "root", "2026-06-01T00:00:00+00:00")
    mem.title_conversation("cv1", "First title")
    mem.title_conversation("cv1", "Second title")  # should not overwrite

    conn = sqlite3.connect(mem.sqlite_path)
    row = conn.execute("SELECT title FROM conversations WHERE id='cv1'").fetchone()
    conn.close()
    assert row[0] == "First title"
