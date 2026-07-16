"""Tests for T-203: auto-file draft tickets from miner candidates."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.passive.ticket_candidate_miner import (
    emit_drafts,
    _draft_from_candidate,
    _load_draft_titles,
    _make_candidate,
)


def _candidate(title="Fix the thing", source="turn_logs", sev="P2"):
    return _make_candidate(source, title, "description of the issue", sev)


# ── _draft_from_candidate ─────────────────────────────────────────────────────

def test_draft_has_required_fields():
    c = _candidate()
    draft = _draft_from_candidate(c, "T-draft-001")
    assert draft["id"] == "T-draft-001"
    assert draft["status"] == "draft"
    assert draft["auto_mined"] is True
    assert "title" in draft
    assert "severity" in draft


def test_draft_title_preserved():
    c = _candidate(title="Investigate memory leak")
    draft = _draft_from_candidate(c, "T-draft-002")
    assert "memory leak" in draft["title"].lower()


def test_draft_caps_title_at_100():
    c = _candidate(title="x" * 200)
    draft = _draft_from_candidate(c, "T-draft-003")
    assert len(draft["title"]) <= 100


# ── emit_drafts ───────────────────────────────────────────────────────────────

def test_emit_drafts_creates_file(tmp_path):
    c = _candidate(title="Unique new bug")
    written = emit_drafts([c], root=tmp_path)
    assert len(written) == 1
    draft_file = tmp_path / "tickets" / "drafts" / written[0]
    assert draft_file.exists()


def test_emit_drafts_deduplicates_against_open(tmp_path):
    # Create a matching open ticket
    open_dir = tmp_path / "tickets" / "open"
    open_dir.mkdir(parents=True)
    (open_dir / "T-999-fix-the-thing.json").write_text(
        json.dumps({"id": "T-999", "title": "Fix the thing", "status": "open"}),
        encoding="utf-8"
    )
    c = _candidate(title="Fix the thing")  # same title (case-insensitive)
    written = emit_drafts([c], root=tmp_path)
    assert len(written) == 0


def test_emit_drafts_deduplicates_against_closed(tmp_path):
    closed_dir = tmp_path / "tickets" / "closed"
    closed_dir.mkdir(parents=True)
    (closed_dir / "T-100-done.json").write_text(
        json.dumps({"id": "T-100", "title": "Already fixed", "status": "closed"}),
        encoding="utf-8"
    )
    c = _candidate(title="Already fixed")
    written = emit_drafts([c], root=tmp_path)
    assert len(written) == 0


def test_emit_drafts_deduplicates_against_existing_drafts(tmp_path):
    drafts_dir = tmp_path / "tickets" / "drafts"
    drafts_dir.mkdir(parents=True)
    (drafts_dir / "T-draft-001-existing.json").write_text(
        json.dumps({"id": "T-draft-001", "title": "Existing draft", "status": "draft"}),
        encoding="utf-8"
    )
    c = _candidate(title="Existing draft")
    written = emit_drafts([c], root=tmp_path)
    assert len(written) == 0


def test_emit_drafts_multiple_unique(tmp_path):
    candidates = [
        _candidate(title=f"Bug number {i}")
        for i in range(3)
    ]
    written = emit_drafts(candidates, root=tmp_path)
    assert len(written) == 3


def test_emit_drafts_valid_json_output(tmp_path):
    c = _candidate(title="Valid JSON draft")
    written = emit_drafts([c], root=tmp_path)
    assert written
    draft_path = tmp_path / "tickets" / "drafts" / written[0]
    data = json.loads(draft_path.read_text(encoding="utf-8"))
    assert data["status"] == "draft"


# ── drafts invisible to sprint ────────────────────────────────────────────────

def test_sprint_list_open_ignores_drafts(tmp_path):
    """sprint.list_open_tickets must never return draft tickets."""
    import scripts.sprint as sprint

    # Write a draft directly into tickets/drafts/
    drafts_dir = tmp_path / "tickets" / "drafts"
    drafts_dir.mkdir(parents=True)
    (drafts_dir / "T-draft-001-test.json").write_text(
        json.dumps({"id": "T-draft-001", "title": "Draft ticket", "status": "draft"}),
        encoding="utf-8"
    )

    # Write a real open ticket
    open_dir = tmp_path / "tickets" / "open"
    open_dir.mkdir(parents=True)
    (open_dir / "T-500-real.json").write_text(
        json.dumps({"id": "T-500", "title": "Real ticket", "status": "open",
                    "severity": "P3", "created": "2026-06-13T00:00:00+00:00"}),
        encoding="utf-8"
    )

    orig = sprint.TICKETS_OPEN
    sprint.TICKETS_OPEN = open_dir
    try:
        tickets = sprint.list_open_tickets()
        ids = [t["id"] for t in tickets]
        assert "T-draft-001" not in ids
        assert "T-500" in ids
    finally:
        sprint.TICKETS_OPEN = orig
