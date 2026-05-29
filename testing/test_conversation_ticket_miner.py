"""testing/test_conversation_ticket_miner.py — T-127: passive Skill 14 tests."""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _seed_turns(tmp_path: Path, entries):
    log = tmp_path / "logs" / "turns.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return log


def _seed_tickets(tmp_path: Path, titles_open=None, titles_closed=None):
    for sub, titles in (("open", titles_open or []), ("closed", titles_closed or [])):
        d = tmp_path / "tickets" / sub
        d.mkdir(parents=True, exist_ok=True)
        for i, t in enumerate(titles):
            (d / f"T-{i:03d}-x.json").write_text(json.dumps({"id": f"T-{i:03d}", "title": t}), encoding="utf-8")


def _now_iso(offset_hours=0):
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


# ── dedup ─────────────────────────────────────────────────────────────────────

def test_dedup_filters_near_match():
    from scripts.passive.conversation_ticket_miner import _dedup_against_existing
    candidates = [
        {"title": "Fix vision provider chain to fall back on 429"},
        {"title": "Add new audio transcription feature"},
    ]
    existing = ["fix vision provider chain to fall back on quota"]
    fresh = _dedup_against_existing(candidates, existing, threshold=0.6)
    assert len(fresh) == 1
    assert "audio" in fresh[0]["title"]


def test_dedup_keeps_distinct():
    from scripts.passive.conversation_ticket_miner import _dedup_against_existing
    candidates = [{"title": "Brand new feature"}]
    existing = ["completely unrelated work"]
    fresh = _dedup_against_existing(candidates, existing, threshold=0.7)
    assert len(fresh) == 1


# ── tail turns ────────────────────────────────────────────────────────────────

def test_tail_turns_filters_by_24h(tmp_path):
    from scripts.passive.conversation_ticket_miner import _tail_turns
    _seed_turns(tmp_path, [
        {"ts": _now_iso(offset_hours=48), "user_input": "old", "response_preview": "old"},
        {"ts": _now_iso(offset_hours=1), "user_input": "recent", "response_preview": "recent"},
    ])
    turns = _tail_turns(tmp_path, hours=24)
    assert len(turns) == 1
    assert turns[0]["user_input"] == "recent"


# ── status thresholds ────────────────────────────────────────────────────────

def test_pass_when_no_candidates(tmp_path):
    from scripts.passive.conversation_ticket_miner import run_check
    from scripts.passive.common import Status
    _seed_turns(tmp_path, [{"ts": _now_iso(1), "user_input": "hi", "response_preview": "hello"}])
    _seed_tickets(tmp_path)

    with patch("scripts.passive.conversation_ticket_miner._call_groq", return_value=[]), \
         patch("scripts.passive.conversation_ticket_miner.write_report", lambda *a, **kw: None):
        status = run_check(root=tmp_path, reports=tmp_path / "reports")

    assert status == Status.PASS


def test_warn_when_1_to_3_candidates(tmp_path):
    from scripts.passive.conversation_ticket_miner import run_check
    from scripts.passive.common import Status
    _seed_turns(tmp_path, [{"ts": _now_iso(1), "user_input": "hi", "response_preview": "hello"}])
    _seed_tickets(tmp_path)

    fake_candidates = [{"title": "Fix X", "rationale": "Y"}, {"title": "Fix Z", "rationale": "Q"}]
    with patch("scripts.passive.conversation_ticket_miner._call_groq", return_value=fake_candidates), \
         patch("scripts.passive.conversation_ticket_miner.write_report", lambda *a, **kw: None):
        status = run_check(root=tmp_path, reports=tmp_path / "reports")

    assert status == Status.WARN


def test_fail_when_4_plus_candidates(tmp_path):
    from scripts.passive.conversation_ticket_miner import run_check
    from scripts.passive.common import Status
    _seed_turns(tmp_path, [{"ts": _now_iso(1), "user_input": "hi", "response_preview": "hello"}])
    _seed_tickets(tmp_path)

    fake = [{"title": f"item {i}", "rationale": "x"} for i in range(5)]
    with patch("scripts.passive.conversation_ticket_miner._call_groq", return_value=fake), \
         patch("scripts.passive.conversation_ticket_miner.write_report", lambda *a, **kw: None):
        status = run_check(root=tmp_path, reports=tmp_path / "reports")

    assert status == Status.FAIL


def test_dedup_against_existing_titles(tmp_path):
    """Candidates matching existing ticket titles are filtered out."""
    from scripts.passive.conversation_ticket_miner import run_check
    from scripts.passive.common import Status

    _seed_turns(tmp_path, [{"ts": _now_iso(1), "user_input": "hi", "response_preview": "hello"}])
    _seed_tickets(tmp_path, titles_closed=["Add caching to vision provider chain"])

    fake = [
        {"title": "Add caching to vision provider chain", "rationale": "dup"},
        {"title": "Completely new feature about cats", "rationale": "fresh"},
    ]
    with patch("scripts.passive.conversation_ticket_miner._call_groq", return_value=fake), \
         patch("scripts.passive.conversation_ticket_miner.write_report", lambda *a, **kw: None):
        status = run_check(root=tmp_path, reports=tmp_path / "reports")

    # Only 1 fresh → WARN
    assert status == Status.WARN
    # Verify the fresh one was written
    cand_path = tmp_path / "analysis" / "conversation_candidates.jsonl"
    assert cand_path.exists()
    lines = [l for l in cand_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert "cats" in rec["title"]


# ── registration ─────────────────────────────────────────────────────────────

def test_daily_digest_registers_skill_14():
    from scripts.passive.passive_daily_digest import SKILL_MODULES
    names = [m[0] for m in SKILL_MODULES]
    assert "conversation_ticket_miner" in names
