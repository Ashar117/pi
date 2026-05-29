"""testing/test_close_ticket.py — T-128: close-ticket gate tests."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_ticket(tmp_path, tid="T-999", adr_required=None):
    open_dir = tmp_path / "tickets" / "open"
    open_dir.mkdir(parents=True)
    ticket = {
        "id": tid,
        "title": "test ticket",
        "status": "open",
        "effort_estimate": "1h",
    }
    if adr_required:
        ticket["adr_required"] = adr_required
    path = open_dir / f"{tid}-test.json"
    path.write_text(json.dumps(ticket, indent=2), encoding="utf-8")
    return path, ticket


def _make_solutions(tmp_path, entries=None):
    sol_dir = tmp_path / "solutions"
    sol_dir.mkdir(parents=True)
    sol_path = sol_dir / "SOLUTIONS.jsonl"
    if entries is None:
        entries = []
    sol_path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return sol_path


# ── find_ticket ───────────────────────────────────────────────────────────────

def test_find_ticket_in_open(tmp_path):
    from scripts import close_ticket as ct
    path, _ = _make_ticket(tmp_path)
    with patch.object(ct, "OPEN_DIR", tmp_path / "tickets" / "open"), \
         patch.object(ct, "CLOSED_DIR", tmp_path / "tickets" / "closed"):
        (tmp_path / "tickets" / "closed").mkdir(parents=True)
        found, loc = ct.find_ticket("T-999")
    assert loc == "open"
    assert found == path


def test_find_ticket_missing(tmp_path):
    from scripts import close_ticket as ct
    (tmp_path / "tickets" / "open").mkdir(parents=True)
    (tmp_path / "tickets" / "closed").mkdir(parents=True)
    with patch.object(ct, "OPEN_DIR", tmp_path / "tickets" / "open"), \
         patch.object(ct, "CLOSED_DIR", tmp_path / "tickets" / "closed"):
        found, loc = ct.find_ticket("T-999")
    assert loc == "missing"
    assert found is None


# ── gate: solution recorded ───────────────────────────────────────────────────

def test_solution_gate_fails_when_missing(tmp_path):
    from scripts import close_ticket as ct
    sol_path = _make_solutions(tmp_path, entries=[
        {"id": "S-001", "ticket_ids": ["T-OTHER"]},
    ])
    ticket = {"id": "T-999"}
    args = MagicMock(no_solution=False)
    with patch.object(ct, "SOLUTIONS_PATH", sol_path):
        result = ct.gate_solution_recorded(ticket, args)
    assert not result.passed
    assert "no entry" in result.detail


def test_solution_gate_passes_when_linked(tmp_path):
    from scripts import close_ticket as ct
    sol_path = _make_solutions(tmp_path, entries=[
        {"id": "S-001", "ticket_ids": ["T-999"]},
    ])
    ticket = {"id": "T-999"}
    args = MagicMock(no_solution=False)
    with patch.object(ct, "SOLUTIONS_PATH", sol_path):
        result = ct.gate_solution_recorded(ticket, args)
    assert result.passed


def test_solution_gate_bypassed_with_no_solution_flag():
    from scripts import close_ticket as ct
    ticket = {"id": "T-999"}
    args = MagicMock(no_solution=True)
    result = ct.gate_solution_recorded(ticket, args)
    assert result.passed
    assert "skipped" in result.detail


# ── gate: ADR present ────────────────────────────────────────────────────────

def test_adr_gate_passes_when_not_required(tmp_path):
    from scripts import close_ticket as ct
    ticket = {"id": "T-999"}
    args = MagicMock()
    result = ct.gate_adr_present(ticket, args)
    assert result.passed


def test_adr_gate_fails_when_required_but_missing(tmp_path):
    from scripts import close_ticket as ct
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    ticket = {"id": "T-999", "adr_required": "ADR-007"}
    args = MagicMock()
    with patch.object(ct, "ADR_DIR", adr_dir):
        result = ct.gate_adr_present(ticket, args)
    assert not result.passed


def test_adr_gate_passes_when_file_exists(tmp_path):
    from scripts import close_ticket as ct
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "007-something.md").write_text("# adr", encoding="utf-8")
    ticket = {"id": "T-999", "adr_required": "ADR-007"}
    args = MagicMock()
    with patch.object(ct, "ADR_DIR", adr_dir):
        result = ct.gate_adr_present(ticket, args)
    assert result.passed


# ── verify gate ──────────────────────────────────────────────────────────────

def test_verify_gate_skipped_with_flag():
    from scripts import close_ticket as ct
    args = MagicMock(skip_verify=True)
    result = ct.gate_verify_pass({}, args)
    assert result.passed
    assert "skipped" in result.detail


# ── end-to-end: idempotent close ─────────────────────────────────────────────

def test_already_closed_idempotent(tmp_path):
    """If ticket is already in closed/, script exits 0 without re-doing anything."""
    from scripts import close_ticket as ct
    closed_dir = tmp_path / "tickets" / "closed"
    closed_dir.mkdir(parents=True)
    (tmp_path / "tickets" / "open").mkdir(parents=True)
    (closed_dir / "T-999-test.json").write_text(json.dumps({"id": "T-999", "status": "closed"}), encoding="utf-8")

    with patch.object(ct, "OPEN_DIR", tmp_path / "tickets" / "open"), \
         patch.object(ct, "CLOSED_DIR", closed_dir):
        found, loc = ct.find_ticket("T-999")
    assert loc == "closed"
