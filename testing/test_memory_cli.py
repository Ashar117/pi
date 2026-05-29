"""testing/test_memory_cli.py — T-133: memory_cli.py subcommand tests.

Uses a seeded temp SQLite DB — no real pi.db, no PiAgent needed.
"""
import io
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.memory_cli import (
    cmd_list,
    cmd_forget,
    cmd_pin,
    cmd_why,
    _db_path,
    _find_by_prefix,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _seed_db(path: Path, rows: list[dict] | None = None) -> None:
    """Create minimal l3_cache table and insert rows."""
    con = sqlite3.connect(str(path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS l3_cache (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            importance INTEGER,
            category TEXT,
            active_until TEXT,
            created_at TEXT,
            invalid_at TEXT,
            kind TEXT,
            source_id TEXT,
            superseded_by TEXT,
            formula TEXT
        )
    """)
    for row in (rows or []):
        con.execute(
            "INSERT INTO l3_cache (id, content, importance, category, created_at, invalid_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [row["id"], row["content"], row.get("importance", 5),
             row.get("category", "note"), row.get("created_at", "2026-01-01T00:00:00Z"),
             row.get("invalid_at")],
        )
    con.commit()
    con.close()


def _make_args(**kwargs):
    """Build SimpleNamespace mimicking argparse output."""
    defaults = {
        "god": False,
        "json": False,
        "full_id": False,
        "category": "",
        "limit": 20,
        "include_archived": False,
        "yes": True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.fixture
def db(tmp_path):
    """Temp DB path seeded with 3 L3 rows."""
    p = tmp_path / "pi.db"
    _seed_db(p, [
        {"id": "aaaa0001-0000-0000-0000-000000000000", "content": "Ash studies at GSU",
         "importance": 9, "category": "permanent_profile"},
        {"id": "bbbb0002-0000-0000-0000-000000000000", "content": "Ash likes pizza",
         "importance": 4, "category": "preferences"},
        {"id": "cccc0003-0000-0000-0000-000000000000", "content": "Old stale note",
         "importance": 2, "category": "note",
         "invalid_at": "2026-03-01T00:00:00Z"},
    ])
    return p


# ── list ──────────────────────────────────────────────────────────────────────

def test_list_returns_active_rows(db, capsys):
    args = _make_args()
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_list(args)
    out = capsys.readouterr().out
    assert "GSU" in out
    assert "pizza" in out
    assert "stale note" not in out  # invalidated row filtered


def test_list_include_archived(db, capsys):
    args = _make_args(include_archived=True)
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_list(args)
    out = capsys.readouterr().out
    assert "stale note" in out


def test_list_category_filter(db, capsys):
    args = _make_args(category="preferences")
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_list(args)
    out = capsys.readouterr().out
    assert "pizza" in out
    assert "GSU" not in out


def test_list_json_output(db, capsys):
    args = _make_args(json=True)
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_list(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert any("GSU" in row.get("content", "") for row in data)


def test_list_full_id(db, capsys):
    args = _make_args(full_id=True)
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_list(args)
    out = capsys.readouterr().out
    assert "aaaa0001-0000-0000-0000-000000000000" in out


# ── forget ────────────────────────────────────────────────────────────────────

def test_forget_invalidates_matching_row(db):
    args = _make_args(query="pizza preferences", yes=True)
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_forget(args)

    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT invalid_at FROM l3_cache WHERE id = 'bbbb0002-0000-0000-0000-000000000000'"
    ).fetchone()
    con.close()
    assert row[0] is not None, "Row should be invalidated"


def test_forget_no_match_is_silent(db, capsys):
    args = _make_args(query="zyxwvutsrqp", yes=True)
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_forget(args)
    out = capsys.readouterr().out
    assert "No rows matching" in out


def test_forget_confirm_cancel(db, monkeypatch):
    """User types 'n' → no rows invalidated."""
    monkeypatch.setattr("builtins.input", lambda _: "n")
    args = _make_args(query="GSU", yes=False)
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_forget(args)

    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT invalid_at FROM l3_cache WHERE id = 'aaaa0001-0000-0000-0000-000000000000'"
    ).fetchone()
    con.close()
    assert row[0] is None, "Row must still be active after cancel"


# ── pin ───────────────────────────────────────────────────────────────────────

def test_pin_sets_importance_10(db, capsys):
    args = _make_args()
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_pin(SimpleNamespace(god=False, id_prefix="bbbb0002"))
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT importance FROM l3_cache WHERE id = 'bbbb0002-0000-0000-0000-000000000000'"
    ).fetchone()
    con.close()
    assert row[0] == 10


def test_pin_no_op_when_pinned_column_missing(db, capsys):
    """pinned column doesn't exist yet (pre-T-135) — should not crash."""
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_pin(SimpleNamespace(god=False, id_prefix="aaaa0001"))
    out = capsys.readouterr().out
    assert "importance=10" in out


def test_pin_unknown_prefix_exits(db):
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        with pytest.raises(SystemExit):
            cmd_pin(SimpleNamespace(god=False, id_prefix="zzzznotfound"))


# ── why ───────────────────────────────────────────────────────────────────────

def test_why_prints_provenance(db, capsys):
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_why(SimpleNamespace(god=False, id_prefix="aaaa0001", json=False))
    out = capsys.readouterr().out
    assert "aaaa0001-0000-0000-0000-000000000000" in out
    assert "permanent_profile" in out
    assert "GSU" in out


def test_why_json_output(db, capsys):
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_why(SimpleNamespace(god=False, id_prefix="aaaa0001", json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "aaaa0001-0000-0000-0000-000000000000"
    assert data["category"] == "permanent_profile"


def test_why_unknown_prefix_exits(db):
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        with pytest.raises(SystemExit):
            cmd_why(SimpleNamespace(god=False, id_prefix="zzzznothere", json=False))


# ── god flag guard ────────────────────────────────────────────────────────────

def test_god_flag_without_env_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("PI_GOD_CLI", raising=False)
    with pytest.raises(SystemExit) as exc:
        _db_path(god=True)
    assert exc.value.code == 1


def test_god_flag_with_env_returns_god_db(monkeypatch):
    monkeypatch.setenv("PI_GOD_CLI", "1")
    import scripts.memory_cli as mc
    with patch.object(mc, "_GOD_DB", Path("/fake/god_memory.db")):
        p = _db_path(god=True)
    assert "god_memory" in str(p)


def test_god_flag_never_returns_public_db(monkeypatch):
    monkeypatch.setenv("PI_GOD_CLI", "1")
    import scripts.memory_cli as mc
    pub = mc._PUBLIC_DB
    result = _db_path(god=True)
    assert result != pub, "god mode must never return public db path"
