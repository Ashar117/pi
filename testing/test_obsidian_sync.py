"""
testing/test_obsidian_sync.py — offline tests for tools/tools_obsidian.py.

Covers:
- _atomic_write: file appears at destination; .tmp is cleaned up
- sync_l3_to_vault: groups rows by category, writes one .md per category,
  content includes importance bar and entry text
- sync_l3_to_vault: empty cache produces zero files (no error)
- sync_l2_to_vault: groups by category, handles JSONB content dict
- sync_l2_to_vault: Supabase failure returns error but does not raise
- render_tickets_to_vault: reads open/ and closed/ JSON, writes both .md files
- render_tickets_to_vault: missing tickets/ dir does not raise
- render_status_to_vault: copies STATUS.md with sync header
- render_status_to_vault: missing source returns error dict, does not raise
- sync_vault: all steps run; summary dict has expected keys

Offline — uses tempfile for vault, real SQLite for L3, MagicMock for Supabase.
"""
import json
import os
import sqlite3
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_obsidian import (
    _atomic_write,
    _importance_bar,
    render_per_ticket_notes,
    render_status_to_vault,
    render_tickets_to_vault,
    sync_l2_to_vault,
    sync_l3_to_vault,
    sync_vault,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_vault(tmp_path):
    """Return a temporary vault root with the expected subdirectory structure."""
    (tmp_path / "memory" / "L3").mkdir(parents=True)
    (tmp_path / "memory" / "L2").mkdir(parents=True)
    (tmp_path / "notes" / "tickets").mkdir(parents=True)
    (tmp_path / "notes" / "per-ticket").mkdir(parents=True)
    return str(tmp_path)


@pytest.fixture()
def tmp_project(tmp_path):
    """Minimal project root with docs/STATUS.md and empty ticket dirs."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "tickets" / "open").mkdir(parents=True)
    (tmp_path / "tickets" / "closed").mkdir(parents=True)
    (tmp_path / "docs" / "STATUS.md").write_text(
        "# STATUS\n\nAll systems go.\n", encoding="utf-8"
    )
    return str(tmp_path)


def _make_memory_tools(tmp_path, rows=None):
    """Return a minimal mock with a real SQLite l3_cache and a mocked Supabase."""
    from tools.tools_memory import MemoryTools

    db_path = str(tmp_path / "test_l3.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE l3_cache "
        "(id TEXT PRIMARY KEY, content TEXT, importance INTEGER, "
        "category TEXT, active_until TEXT, created_at TEXT)"
    )
    if rows:
        for r in rows:
            conn.execute(
                "INSERT INTO l3_cache VALUES (?,?,?,?,?,?)",
                [r["id"], r["content"], r.get("importance", 5),
                 r.get("category", "note"), r.get("active_until"), r.get("created_at", "")],
            )
    conn.commit()
    conn.close()

    import threading
    mt = MemoryTools.__new__(MemoryTools)
    mt.sqlite_path = db_path
    mt.supabase = MagicMock()
    mt.supabase.table.return_value.select.return_value.eq.return_value\
        .order.return_value.execute.return_value.data = []
    mt._supa_lock = threading.RLock()
    return mt


# ---------------------------------------------------------------------------
# _atomic_write
# ---------------------------------------------------------------------------

def test_atomic_write_creates_file(tmp_path):
    path = str(tmp_path / "sub" / "test.md")
    _atomic_write(path, "hello")
    assert open(path, encoding="utf-8").read() == "hello"


def test_atomic_write_no_tmp_leftover(tmp_path):
    path = str(tmp_path / "test.md")
    _atomic_write(path, "hello")
    assert not os.path.exists(path + ".tmp")


def test_atomic_write_overwrites_existing(tmp_path):
    path = str(tmp_path / "test.md")
    _atomic_write(path, "v1")
    _atomic_write(path, "v2")
    assert open(path, encoding="utf-8").read() == "v2"


# ---------------------------------------------------------------------------
# _importance_bar
# ---------------------------------------------------------------------------

def test_importance_bar_full():
    assert _importance_bar(10) == "XXXXX"


def test_importance_bar_empty():
    assert _importance_bar(0) == "ooooo"


def test_importance_bar_mid():
    bar = _importance_bar(5)
    assert len(bar) == 5
    assert "X" in bar and "o" in bar


# ---------------------------------------------------------------------------
# sync_l3_to_vault
# ---------------------------------------------------------------------------

def test_l3_sync_groups_by_category(tmp_path, tmp_vault):
    rows = [
        {"id": "1", "content": "User likes dark mode", "importance": 8,
         "category": "preferences"},
        {"id": "2", "content": "User is a CS student", "importance": 9,
         "category": "permanent_profile"},
        {"id": "3", "content": "User owns a cat", "importance": 6,
         "category": "preferences"},
    ]
    mt = _make_memory_tools(tmp_path, rows)
    result = sync_l3_to_vault(mt, tmp_vault)

    assert result["error"] is None
    assert result["written"] == 2
    assert set(result["categories"]) == {"preferences", "permanent_profile"}

    pref_path = os.path.join(tmp_vault, "memory", "L3", "preferences.md")
    assert os.path.exists(pref_path)
    pref_content = open(pref_path, encoding="utf-8").read()
    assert "dark mode" in pref_content
    assert "owns a cat" in pref_content

    profile_path = os.path.join(tmp_vault, "memory", "L3", "permanent_profile.md")
    assert "CS student" in open(profile_path, encoding="utf-8").read()


def test_l3_sync_empty_cache_writes_nothing(tmp_path, tmp_vault):
    mt = _make_memory_tools(tmp_path, rows=[])
    result = sync_l3_to_vault(mt, tmp_vault)
    assert result["written"] == 0
    assert result["error"] is None
    assert os.listdir(os.path.join(tmp_vault, "memory", "L3")) == []


def test_l3_sync_bad_sqlite_returns_error(tmp_path, tmp_vault):
    import threading
    from tools.tools_memory import MemoryTools
    mt = MemoryTools.__new__(MemoryTools)
    mt.sqlite_path = str(tmp_path / "does_not_exist.db")
    mt.supabase = MagicMock()
    mt._supa_lock = threading.RLock()
    # SQLite creates empty DB on open; use a non-table name to trigger error
    # Actually sqlite3 won't error on open — we force it by pointing to a dir
    os.makedirs(str(tmp_path / "badpath.db"))
    mt.sqlite_path = str(tmp_path / "badpath.db")
    result = sync_l3_to_vault(mt, tmp_vault)
    assert result["error"] is not None
    assert result["written"] == 0


# ---------------------------------------------------------------------------
# sync_l2_to_vault
# ---------------------------------------------------------------------------

def test_l2_sync_groups_by_category(tmp_path, tmp_vault):
    mt = _make_memory_tools(tmp_path)
    mt.supabase.table.return_value.select.return_value.eq.return_value\
        .order.return_value.execute.return_value.data = [
        {"id": "a", "category": "projects", "title": "GNN research",
         "content": {"text": "Working on graph neural networks"}, "importance": 8,
         "status": "active", "created_at": "2026-01-01"},
        {"id": "b", "category": "technical", "title": "Python setup",
         "content": {"text": "Using Python 3.13"}, "importance": 6,
         "status": "active", "created_at": "2026-01-01"},
    ]
    result = sync_l2_to_vault(mt, tmp_vault)
    assert result["error"] is None
    assert result["written"] == 2
    proj_path = os.path.join(tmp_vault, "memory", "L2", "projects.md")
    assert "graph neural networks" in open(proj_path, encoding="utf-8").read()


def test_l2_sync_supabase_error_returns_error_dict(tmp_path, tmp_vault):
    mt = _make_memory_tools(tmp_path)
    mt.supabase.table.return_value.select.return_value.eq.return_value\
        .order.return_value.execute.side_effect = RuntimeError("network error")
    result = sync_l2_to_vault(mt, tmp_vault)
    assert result["error"] is not None
    assert result["written"] == 0


def test_l2_sync_plain_string_content(tmp_path, tmp_vault):
    """Content field may be a plain string rather than a dict."""
    mt = _make_memory_tools(tmp_path)
    mt.supabase.table.return_value.select.return_value.eq.return_value\
        .order.return_value.execute.return_value.data = [
        {"id": "x", "category": "note", "title": "misc",
         "content": "plain text entry", "importance": 5,
         "status": "active", "created_at": "2026-01-01"},
    ]
    result = sync_l2_to_vault(mt, tmp_vault)
    assert result["error"] is None
    note_path = os.path.join(tmp_vault, "memory", "L2", "note.md")
    assert "plain text entry" in open(note_path, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# render_tickets_to_vault
# ---------------------------------------------------------------------------

def test_ticket_render_writes_both_files(tmp_path, tmp_vault):
    proj = str(tmp_path / "proj")
    os.makedirs(os.path.join(proj, "tickets", "open"))
    os.makedirs(os.path.join(proj, "tickets", "closed"))

    open_ticket = {
        "id": "T-099", "title": "Test bug", "severity": "P2",
        "what_failed": "Something broke", "suggested_fix": "Fix it",
        "status": "open",
    }
    closed_ticket = {
        "id": "T-001", "title": "Old bug", "severity": "P1",
        "linked_solution": "S-001",
        "what_failed": "It was wrong", "fix_summary": "Made it right",
        "status": "closed",
    }
    with open(os.path.join(proj, "tickets", "open", "T-099.json"), "w") as f:
        json.dump(open_ticket, f)
    with open(os.path.join(proj, "tickets", "closed", "T-001.json"), "w") as f:
        json.dump(closed_ticket, f)

    result = render_tickets_to_vault(proj, tmp_vault)
    assert result["error"] is None
    assert result["open"] == 1
    assert result["closed"] == 1

    open_md = open(os.path.join(tmp_vault, "notes", "tickets", "open.md"),
                   encoding="utf-8").read()
    assert "T-099" in open_md
    assert "Test bug" in open_md

    closed_md = open(os.path.join(tmp_vault, "notes", "tickets", "closed.md"),
                     encoding="utf-8").read()
    assert "T-001" in closed_md
    assert "S-001" in closed_md


def test_ticket_render_missing_dirs_returns_zero(tmp_path, tmp_vault):
    proj = str(tmp_path / "empty_proj")
    os.makedirs(proj)
    result = render_tickets_to_vault(proj, tmp_vault)
    assert result["error"] is None
    assert result["open"] == 0
    assert result["closed"] == 0


# ---------------------------------------------------------------------------
# render_status_to_vault
# ---------------------------------------------------------------------------

def test_status_render_copies_file(tmp_path, tmp_vault):
    proj = str(tmp_path / "proj2")
    os.makedirs(os.path.join(proj, "docs"))
    with open(os.path.join(proj, "docs", "STATUS.md"), "w", encoding="utf-8") as f:
        f.write("# STATUS\n\nAll good.\n")

    result = render_status_to_vault(proj, tmp_vault)
    assert result["error"] is None
    assert result["written"] is True

    dst = open(os.path.join(tmp_vault, "notes", "status.md"), encoding="utf-8").read()
    assert "All good" in dst
    assert "synced from docs/STATUS.md" in dst


def test_status_render_missing_source_returns_error(tmp_path, tmp_vault):
    proj = str(tmp_path / "no_docs")
    os.makedirs(proj)
    result = render_status_to_vault(proj, tmp_vault)
    assert result["written"] is False
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# render_per_ticket_notes
# ---------------------------------------------------------------------------

def test_per_ticket_notes_writes_briefs(tmp_path, tmp_vault):
    proj = str(tmp_path / "proj_pt")
    closed = os.path.join(proj, "tickets", "closed")
    os.makedirs(closed)

    ticket = {
        "id": "T-042",
        "title": "Cache invalidation bug",
        "severity": "P2",
        "closed": "2026-04-01T12:00:00Z",
        "linked_solution": "S-040",
        "what_failed": "Cache was not cleared on user logout",
        "where_failed": "tools/cache.py:88",
        "why_likely": "TTL set too long, no explicit eviction on logout",
        "fix_summary": "Added explicit cache.clear() call in logout handler",
        "verification": {
            "test": "testing/test_cache.py",
            "result": "3 tests passed",
        },
    }
    with open(os.path.join(closed, "T-042-cache-invalidation-bug.json"), "w") as f:
        json.dump(ticket, f)

    result = render_per_ticket_notes(proj, tmp_vault)
    assert result["error"] is None
    assert result["written"] == 1

    out = os.path.join(tmp_vault, "notes", "per-ticket", "T-042-cache-invalidation-bug.md")
    assert os.path.exists(out)
    content = open(out, encoding="utf-8").read()
    assert "T-042" in content
    assert "Cache invalidation bug" in content
    assert "S-040" in content
    assert "logout" in content
    assert "testing/test_cache.py" in content


def test_per_ticket_notes_missing_closed_dir(tmp_path, tmp_vault):
    proj = str(tmp_path / "empty_proj")
    os.makedirs(proj)
    result = render_per_ticket_notes(proj, tmp_vault)
    assert result["written"] == 0
    assert result["error"] is None


# ---------------------------------------------------------------------------
# sync_vault (integration)
# ---------------------------------------------------------------------------

def test_sync_vault_returns_all_keys(tmp_path, tmp_vault, tmp_project, monkeypatch):
    """sync_vault summary must contain l3, l2, tickets, status, elapsed_s."""
    mt = _make_memory_tools(tmp_path)
    monkeypatch.setattr(
        "tools.tools_obsidian._default_vault_root", lambda: tmp_vault
    )
    result = sync_vault(mt, project_root=tmp_project)
    for key in ("l3", "l2", "tickets", "per_ticket", "status", "elapsed_s"):
        assert key in result, f"sync_vault result missing key: {key!r}"
    assert isinstance(result["elapsed_s"], float)


def test_sync_vault_l2_failure_does_not_block_tickets(tmp_path, tmp_vault,
                                                       tmp_project, monkeypatch):
    """If L2 Supabase fails, ticket render must still succeed."""
    mt = _make_memory_tools(tmp_path)
    mt.supabase.table.return_value.select.return_value.eq.return_value\
        .order.return_value.execute.side_effect = RuntimeError("Supabase down")
    monkeypatch.setattr(
        "tools.tools_obsidian._default_vault_root", lambda: tmp_vault
    )
    result = sync_vault(mt, project_root=tmp_project)
    assert result["l2"].get("error") is not None
    assert result["tickets"].get("error") is None
    assert result["status"].get("written") is True
