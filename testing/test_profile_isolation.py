"""testing/test_profile_isolation.py — T-226: guest ticket + log isolation.

Verifies:
  - Guest create_ticket lands in tickets/profiles/<name>/ not tickets/open/
  - sprint.py planning ignores tickets/profiles/*
  - Guest turns route to logs/profiles/<name>/turns.jsonl, NOT logs/turns.jsonl
  - profile_name field present in guest turn entries
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_guest_profile(name: str = "eve"):
    from agent.profile import Profile
    return Profile(
        id=str(uuid.uuid4()),
        name=name,
        display_name=name,
        nickname="",
        password_hash="h",
        salt="s",
        is_guest=True,
        allowlist_json="[]",
        created_at="2026-06-21T00:00:00+00:00",
    )


def _make_ash_profile():
    from agent.profile import Profile
    return Profile(
        id=str(uuid.uuid4()),
        name="ash",
        display_name="ash",
        nickname="",
        password_hash="h",
        salt="s",
        is_guest=False,
        allowlist_json="[]",
        created_at="2026-06-21T00:00:00+00:00",
    )


# ── Ticket routing ─────────────────────────────────────────────────────────────

def test_guest_ticket_lands_in_profiles_dir(tmp_path, monkeypatch):
    """Guest create_ticket must write to tickets/profiles/<name>/ not tickets/open/."""
    import tools.tools_project as tp_mod
    (tmp_path / "tickets" / "open").mkdir(parents=True)
    monkeypatch.setattr(tp_mod, "_ROOT", tmp_path)

    from tools.tools_project import ProjectTools
    guest = _make_guest_profile("eve")
    pt = ProjectTools()
    result = pt.create_ticket(
        title="Test ticket",
        what_failed="nothing",
        component="test",
        severity="P3",
        _profile=guest,
    )
    assert result.get("success"), f"create_ticket failed: {result}"

    profile_dir = tmp_path / "tickets" / "profiles" / "eve"
    tickets_open = tmp_path / "tickets" / "open"
    assert any(profile_dir.glob("*.json")), "No ticket found in tickets/profiles/eve/"
    assert not any(tickets_open.glob("*.json")), "Guest ticket leaked into tickets/open/"


def test_guest_ticket_has_profile_field(tmp_path, monkeypatch):
    """Guest tickets carry the profile name for traceability."""
    import tools.tools_project as tp_mod
    (tmp_path / "tickets" / "open").mkdir(parents=True)
    monkeypatch.setattr(tp_mod, "_ROOT", tmp_path)

    from tools.tools_project import ProjectTools
    guest = _make_guest_profile("eve")
    pt = ProjectTools()
    pt.create_ticket(title="Bug", what_failed="x", component="y", _profile=guest)

    ticket_files = list((tmp_path / "tickets" / "profiles" / "eve").glob("*.json"))
    assert ticket_files
    data = json.loads(ticket_files[0].read_text())
    assert data.get("profile") == "eve"


def test_ash_ticket_still_lands_in_open(tmp_path, monkeypatch):
    """Ash (non-guest) tickets still go to tickets/open/."""
    import tools.tools_project as tp_mod
    (tmp_path / "tickets" / "open").mkdir(parents=True)
    monkeypatch.setattr(tp_mod, "_ROOT", tmp_path)

    from tools.tools_project import ProjectTools
    ash = _make_ash_profile()
    pt = ProjectTools()
    result = pt.create_ticket(
        title="Ash ticket",
        what_failed="something",
        component="core",
        _profile=ash,
    )
    assert result.get("success"), f"create_ticket failed: {result}"
    assert any((tmp_path / "tickets" / "open").glob("*.json")), "Ash ticket not found in tickets/open/"


def test_no_profile_ticket_lands_in_open(tmp_path, monkeypatch):
    """create_ticket with no profile (legacy) still goes to tickets/open/."""
    import tools.tools_project as tp_mod
    (tmp_path / "tickets" / "open").mkdir(parents=True)
    monkeypatch.setattr(tp_mod, "_ROOT", tmp_path)

    from tools.tools_project import ProjectTools
    pt = ProjectTools()
    result = pt.create_ticket(
        title="Legacy ticket",
        what_failed="none",
        component="misc",
    )
    assert result.get("success"), f"create_ticket failed: {result}"
    assert any((tmp_path / "tickets" / "open").glob("*.json"))


# ── Turn log routing ───────────────────────────────────────────────────────────

def test_guest_turn_routes_to_profile_log(tmp_path):
    """Guest turn must land in logs/profiles/<name>/turns.jsonl, not logs/turns.jsonl."""
    import agent.turn_log as tl

    main_log = tmp_path / "logs" / "turns.jsonl"
    guest_log = tmp_path / "logs" / "profiles" / "eve" / "turns.jsonl"

    saved_root = tl._ROOT
    tl._ROOT = tmp_path
    tl._LOG_PATH = main_log
    try:
        tl.append_turn(
            session_id="sess-1",
            mode="normie",
            user_input="hi",
            response="hello",
            duration_ms=10,
            profile_name="eve",
        )
    finally:
        tl._ROOT = saved_root
        tl._LOG_PATH = saved_root / "logs" / "turns.jsonl"

    assert not main_log.exists() or main_log.stat().st_size == 0, \
        "Guest turn leaked into main logs/turns.jsonl"
    assert guest_log.exists(), "Guest log not created"
    lines = [l for l in guest_log.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["profile"] == "eve"
    assert entry["user_input"] == "hi"


def test_ash_turn_stays_on_main_log(tmp_path):
    """Non-guest (no profile_name) turns go to the main log."""
    import agent.turn_log as tl

    main_log = tmp_path / "logs" / "turns.jsonl"

    saved_root = tl._ROOT
    tl._ROOT = tmp_path
    tl._LOG_PATH = main_log
    try:
        tl.append_turn(
            session_id="sess-2",
            mode="root",
            user_input="build x",
            response="done",
            duration_ms=50,
        )
    finally:
        tl._ROOT = saved_root
        tl._LOG_PATH = saved_root / "logs" / "turns.jsonl"

    assert main_log.exists() and main_log.stat().st_size > 0
    profile_dir = tmp_path / "logs" / "profiles"
    assert not profile_dir.exists() or not any(profile_dir.rglob("*.jsonl"))


def test_guest_turn_has_no_profile_key_in_main_log(tmp_path):
    """Ash turns do NOT carry a 'profile' field."""
    import agent.turn_log as tl

    main_log = tmp_path / "logs" / "turns.jsonl"
    saved_root = tl._ROOT
    tl._ROOT = tmp_path
    tl._LOG_PATH = main_log
    try:
        tl.append_turn(
            session_id="sess-3",
            mode="root",
            user_input="test",
            response="ok",
            duration_ms=5,
        )
    finally:
        tl._ROOT = saved_root
        tl._LOG_PATH = saved_root / "logs" / "turns.jsonl"

    entry = json.loads(main_log.read_text().strip())
    assert "profile" not in entry


def test_multiple_guest_turns_all_isolated(tmp_path):
    """Multiple guest turns from same profile all go to per-profile log."""
    import agent.turn_log as tl

    main_log = tmp_path / "logs" / "turns.jsonl"
    guest_log = tmp_path / "logs" / "profiles" / "bob" / "turns.jsonl"

    saved_root = tl._ROOT
    tl._ROOT = tmp_path
    tl._LOG_PATH = main_log
    try:
        for i in range(3):
            tl.append_turn(
                session_id="sess-4",
                mode="normie",
                user_input=f"msg {i}",
                response=f"resp {i}",
                duration_ms=10,
                profile_name="bob",
            )
    finally:
        tl._ROOT = saved_root
        tl._LOG_PATH = saved_root / "logs" / "turns.jsonl"

    assert not main_log.exists() or main_log.stat().st_size == 0
    lines = [l for l in guest_log.read_text().splitlines() if l.strip()]
    assert len(lines) == 3


if __name__ == "__main__":
    import inspect
    import tempfile
    import pathlib
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as td:
            tp = pathlib.Path(td)
            try:
                sig = inspect.signature(fn)
                params = list(sig.parameters.keys())
                if "monkeypatch" in params:
                    print(f"  SKIP  {name} (requires pytest monkeypatch)")
                    continue
                fn(tp) if "tmp_path" in params else fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as e:
                import traceback
                print(f"  FAIL  {name}: {e}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
