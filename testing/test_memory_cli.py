"""testing/test_memory_cli.py — T-133: memory_cli.py subcommand tests.

Uses a seeded temp SQLite DB — no real pi.db, no PiAgent needed.
"""
import io
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
    cmd_forgotten,
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


# ── forgotten (T-301) ─────────────────────────────────────────────────────────

def _seed_forgotten_db(tmp_path):
    """id_expired (active_until in the past), id_contradicted (invalid_at set),
    id_merged (superseded_by -> id_winner), id_winner, id_healthy (none set)."""
    p = tmp_path / "pi.db"
    con = sqlite3.connect(str(p))
    con.execute("""
        CREATE TABLE l3_cache (
            id TEXT PRIMARY KEY, content TEXT NOT NULL, importance INTEGER,
            category TEXT, active_until TEXT, created_at TEXT, invalid_at TEXT,
            superseded_by TEXT
        )
    """)
    now = datetime.now(timezone.utc)
    rows = [
        ("id-expired", "the cafe wifi was FISH123", 6, "note",
         (now - timedelta(hours=2)).isoformat(), None, None),
        ("id-contradicted", "old address: 12 Elm St", 7, "note",
         None, (now - timedelta(hours=1)).isoformat(), None),
        ("id-winner", "current address: 45 Oak Ave", 7, "note",
         None, None, None),
        ("id-merged", "current address: 45 Oak Avenue", 5, "note",
         None, None, "id-winner"),
        ("id-healthy", "my sister lives in Boston", 6, "note",
         None, None, None),
    ]
    for rid, content, imp, cat, active_until, invalid_at, superseded_by in rows:
        con.execute(
            "INSERT INTO l3_cache (id, content, importance, category, "
            "active_until, created_at, invalid_at, superseded_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [rid, content, imp, cat, active_until, "2026-01-01T00:00:00Z",
             invalid_at, superseded_by],
        )
    con.commit()
    con.close()
    return p


def test_forgotten_classifies_each_reason(tmp_path, capsys):
    db = _seed_forgotten_db(tmp_path)
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_forgotten(SimpleNamespace(days=7, json=True))
    data = json.loads(capsys.readouterr().out)

    by_id = {d["id"]: d for d in data}
    assert by_id["id-expired"]["reason"] == "EXPIRED"
    assert by_id["id-contradicted"]["reason"] == "CONTRADICTED"
    assert by_id["id-merged"]["reason"] == "MERGED"
    assert by_id["id-merged"]["superseded_by_snippet"].startswith("current address: 45 Oak Ave")
    assert "id-healthy" not in by_id, "a row with no forgetting signal must never appear"
    assert "id-winner" not in by_id, "the winner itself was never forgotten"


def test_forgotten_precedence_invalid_at_beats_expired(tmp_path, capsys):
    """A row with BOTH invalid_at set and active_until passed must classify
    as CONTRADICTED, not EXPIRED (documented precedence)."""
    p = tmp_path / "pi.db"
    con = sqlite3.connect(str(p))
    con.execute("""
        CREATE TABLE l3_cache (
            id TEXT PRIMARY KEY, content TEXT, importance INTEGER, category TEXT,
            active_until TEXT, created_at TEXT, invalid_at TEXT, superseded_by TEXT
        )
    """)
    now = datetime.now(timezone.utc)
    con.execute(
        "INSERT INTO l3_cache (id, content, active_until, invalid_at) VALUES (?, ?, ?, ?)",
        ["id-both", "expired and contradicted",
         (now - timedelta(hours=3)).isoformat(), (now - timedelta(hours=1)).isoformat()],
    )
    con.commit()
    con.close()

    with patch("scripts.memory_cli._PUBLIC_DB", p):
        cmd_forgotten(SimpleNamespace(days=7, json=True))
    data = json.loads(capsys.readouterr().out)

    assert len(data) == 1
    assert data[0]["reason"] == "CONTRADICTED"


def test_forgotten_days_window_excludes_old_entries(tmp_path, capsys):
    db = _seed_forgotten_db(tmp_path)
    # Push id-contradicted's invalid_at outside a 1-hour window's cutoff
    # by asking for a window shorter than the 1-hour-ago timestamp seeded.
    with patch("scripts.memory_cli._PUBLIC_DB", db):
        cmd_forgotten(SimpleNamespace(days=0, json=True))
    data = json.loads(capsys.readouterr().out)

    by_id = {d["id"] for d in data}
    assert "id-contradicted" not in by_id, "1-hour-old entry must fall outside a 0-day window"
    assert "id-expired" not in by_id
    # MERGED has no timestamp to window on — always included by design.
    assert "id-merged" in by_id


def test_forgotten_empty_prints_message(tmp_path, capsys):
    p = tmp_path / "pi.db"
    con = sqlite3.connect(str(p))
    con.execute("""
        CREATE TABLE l3_cache (
            id TEXT PRIMARY KEY, content TEXT, importance INTEGER, category TEXT,
            active_until TEXT, created_at TEXT, invalid_at TEXT, superseded_by TEXT
        )
    """)
    con.execute("INSERT INTO l3_cache (id, content) VALUES ('h1', 'healthy fact')")
    con.commit()
    con.close()

    with patch("scripts.memory_cli._PUBLIC_DB", p):
        cmd_forgotten(SimpleNamespace(days=7, json=False))
    out = capsys.readouterr().out

    assert "Nothing forgotten" in out


# ── T-304: MemoryTools.forgotten_ledger — the shared classifier itself ───────
# cmd_forgotten is now a thin renderer over this method (dashboard's
# /memory/forgotten endpoint calls the same method) — this test exercises the
# API directly, not through the CLI wrapper.

def test_forgotten_ledger_classifies_directly(tmp_path):
    from tools.tools_memory import MemoryTools
    db = _seed_forgotten_db(tmp_path)
    mt = MemoryTools(supabase_url="", supabase_key="", sqlite_path=str(db))

    ledger = mt.forgotten_ledger(days=7)
    by_id = {d["id"]: d for d in ledger}

    assert by_id["id-expired"]["reason"] == "EXPIRED"
    assert by_id["id-contradicted"]["reason"] == "CONTRADICTED"
    assert by_id["id-merged"]["reason"] == "MERGED"
    assert by_id["id-merged"]["superseded_by_snippet"].startswith("current address: 45 Oak Ave")
    assert "id-healthy" not in by_id
    assert "id-winner" not in by_id


def test_forgotten_ledger_classifies_decayed_from_archive(tmp_path):
    """T-309: rows already moved to l3_archive by decay-archive must still
    surface in the ledger, tagged DECAYED (not EXPIRED — different reason)."""
    from tools.tools_memory import MemoryTools
    from memory.archive import ensure_l3_archive_table

    db = _seed_forgotten_db(tmp_path)
    mt = MemoryTools(supabase_url="", supabase_key="", sqlite_path=str(db))

    con = sqlite3.connect(str(db))
    ensure_l3_archive_table(con)
    con.execute(
        "INSERT INTO l3_archive (id, content, importance, category, archived_at, archive_reason) "
        "VALUES ('id-decayed', 'unused zebrafish fact', 3, 'note', ?, 'decay')",
        [datetime.now(timezone.utc).isoformat()],
    )
    con.commit()
    con.close()

    ledger = mt.forgotten_ledger(days=7)
    by_id = {d["id"]: d for d in ledger}

    assert by_id["id-decayed"]["reason"] == "DECAYED"
