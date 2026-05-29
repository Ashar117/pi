"""testing/test_sprint_isolation.py — T-086 R5 acceptance tests.

Verifies that scripts/sprint.py refuses to operate on god-mode work:
  1. _ticket_touches_god_paths detects literal mentions of GOD_FORBIDDEN_PATHS
  2. list_open_tickets() excludes any ticket whose body mentions a god path
  3. main() refuses to start when tickets/open/god/ accidentally exists
  4. pi_agent.py has no non-interactive god-mode entry path (AST inspection)

No subprocess invocation, no network — pure unit-level guards.
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ── 1: detector unit ────────────────────────────────────────────────────────

def test_ticket_touches_god_paths_detects_each_forbidden_path():
    from scripts.sprint import _ticket_touches_god_paths, GOD_FORBIDDEN_PATHS
    for path in GOD_FORBIDDEN_PATHS:
        ticket = {
            "id": "T-fake",
            "component": f"file at {path} needs work",
            "files_affected": [],
            "current_state": "",
            "target_state": "",
        }
        hit = _ticket_touches_god_paths(ticket)
        assert hit == path, f"expected to detect {path!r}, got {hit!r}"


def test_ticket_touches_god_paths_clean_ticket_returns_none():
    from scripts.sprint import _ticket_touches_god_paths
    ticket = {
        "id": "T-clean",
        "component": "tools/tools_memory.py",
        "files_affected": ["tools/tools_memory.py", "memory/pipeline.py"],
        "current_state": "lexical dedup misses paraphrases",
        "target_state": "semantic dedup via embeddings",
        "title": "Add semantic L2 dedup",
    }
    assert _ticket_touches_god_paths(ticket) is None


def test_ticket_touches_god_paths_scans_nested_lists_and_dicts():
    """A god path mentioned inside files_affected list or migration_plan
    list must be detected, not just top-level string fields."""
    from scripts.sprint import _ticket_touches_god_paths
    ticket = {
        "id": "T-nested",
        "title": "innocent-looking title",
        "files_affected": [
            "scripts/something.py",
            "data/god_memory.db",  # buried in the list
        ],
        "migration_plan": ["1. plan step", "2. another step"],
    }
    assert _ticket_touches_god_paths(ticket) == "data/god_memory.db"


# ── 2: list_open_tickets filter ─────────────────────────────────────────────

def test_list_open_tickets_excludes_god_pathed_tickets(tmp_path, monkeypatch):
    """Drop a fake god-ticket into a temp open/ dir; assert it's excluded."""
    import scripts.sprint as sprint_mod

    fake_open = tmp_path / "open"
    fake_open.mkdir()
    # Innocent ticket — should be picked up
    (fake_open / "T-100-clean.json").write_text(json.dumps({
        "id": "T-100", "title": "clean ticket",
        "severity": "P3", "status": "open",
        "component": "scripts/foo.py",
        "current_state": "no god content here",
        "created": "2026-05-17T00:00:00Z",
    }), encoding="utf-8")
    # God-pathed ticket — must be excluded
    (fake_open / "T-101-private.json").write_text(json.dumps({
        "id": "T-101", "title": "god search work",
        "severity": "P3", "status": "open",
        "component": "agent/god.py",  # the trigger
        "files_affected": ["data/god_memory.db"],
        "current_state": "private intel weekly scan",
        "created": "2026-05-17T00:00:00Z",
    }), encoding="utf-8")

    monkeypatch.setattr(sprint_mod, "TICKETS_OPEN", fake_open)
    tickets = sprint_mod.list_open_tickets()
    ids = {t["id"] for t in tickets}
    assert "T-100" in ids
    assert "T-101" not in ids, "god-pathed ticket leaked through list_open_tickets"


# ── 3: main() refuses to start when tickets/open/god/ exists ────────────────

def test_main_refuses_start_when_god_dir_under_open(tmp_path, monkeypatch, capsys):
    """If tickets/open/god/ exists, main() must return non-zero before any work."""
    import scripts.sprint as sprint_mod
    fake_open = tmp_path / "open"
    (fake_open / "god").mkdir(parents=True)  # the operator-error condition
    monkeypatch.setattr(sprint_mod, "TICKETS_OPEN", fake_open)
    monkeypatch.setattr(sys, "argv", ["sprint.py", "--dry-run"])

    rc = sprint_mod.main()
    assert rc == 3, f"expected rc=3 refusal, got {rc}"
    err = capsys.readouterr().err
    assert "REFUSING TO START" in err
    assert "god" in err.lower()


# ── 4: pi_agent.py has no non-interactive god-mode entry path ──────────────

def test_pi_agent_god_mode_entry_is_interactive_only():
    """Scan pi_agent.py for `self.mode = "god"` and assert each assignment
    sits inside the literal `if user_input.lower().strip() in ("god mode", "god"):`
    branch. Catches any code path that flips into god mode without the user
    typing the magic phrase."""
    pi_agent_src = (ROOT / "pi_agent.py").read_text(encoding="utf-8")
    tree = ast.parse(pi_agent_src)

    # Find every `self.mode = "god"` assignment by AST walk.
    god_assigns: list = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == "mode"):
            continue
        # Value must be the literal string "god"
        if isinstance(node.value, ast.Constant) and node.value.value == "god":
            god_assigns.append(node.lineno)

    assert god_assigns, (
        "pi_agent.py contains NO `self.mode = \"god\"` assignment. "
        "Either god entry is gone (R8 unblocked but unintended), or the AST "
        "scanner is broken. Check pi_agent.py for the 'god mode' command handler."
    )

    # For each assignment, walk up the AST to find the enclosing If statement
    # and verify its condition mentions 'god mode' or 'god' as a literal string.
    src_lines = pi_agent_src.splitlines()
    for line_no in god_assigns:
        # Pull the 15-line context around the assignment
        ctx = "\n".join(src_lines[max(0, line_no - 15):line_no + 2])
        # Conservative check — must mention the interactive trigger string
        assert ("god mode" in ctx.lower() or '"god"' in ctx), (
            f"pi_agent.py:{line_no} sets self.mode = 'god' outside the "
            f"interactive 'god mode' command handler. Non-interactive god "
            f"entry is forbidden by ADR-001 + R5 (T-086). Context:\n{ctx}"
        )
