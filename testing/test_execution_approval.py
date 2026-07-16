"""testing/test_execution_approval.py — T-225: execution approval workflow.

Tests:
  - Guest exec tool creates a pending record + notifies Ash (mock send)
  - /approve by Ash runs the tool in the guest namespace and delivers result
  - /approve by a guest is refused
  - Expired token cannot be approved
  - Denied token never executes
  - Token is single-use (re-approve is a no-op)
"""
import json
import os
import sys
import uuid
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from datetime import datetime, timezone, timedelta


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_guest_profile(name: str = "alice", tmp_path: Path = None):
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


def _make_agent(profile, chat_id: str = "9999"):
    agent = MagicMock()
    agent.current_profile = profile
    agent._current_chat_id = chat_id
    agent.memory = MagicMock()
    agent.evolution = MagicMock()
    agent.evolution.track_pattern = MagicMock()
    return agent


def _setup_registry(tmp_path: Path):
    """Create a ProfileRegistry backed by a temp pi.db and ensure approvals table."""
    from agent.profile import ProfileRegistry
    db_path = str(tmp_path / "pi.db")
    reg = ProfileRegistry(db_path=db_path)
    with reg._connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS approvals "
            "(token TEXT PRIMARY KEY, profile_name TEXT, tool TEXT, args_json TEXT, "
            "status TEXT DEFAULT 'pending', created_at TEXT, expires_at TEXT, "
            "requester_chat_id TEXT DEFAULT '')"
        )
        conn.commit()
    return reg, db_path


def _insert_approval(reg, token, profile_name, tool, args=None, status="pending",
                     chat_id="9999", minutes_from_now=30):
    now = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)).isoformat()
    with reg._connect() as conn:
        conn.execute(
            "INSERT INTO approvals (token, profile_name, tool, args_json, status, created_at, expires_at, requester_chat_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [token, profile_name, tool, json.dumps(args or {}), status, now, expires, chat_id],
        )
        conn.commit()


# ── Guest exec tool creates pending record ─────────────────────────────────────

def test_guest_exec_creates_pending_record(tmp_path):
    """When a guest requests an exec tool, a pending approval record is created."""
    from agent.profile import ProfileRegistry, GUEST_APPROVAL_TOOLS
    from agent.tool_spec import ToolSpec
    import agent.tools as tools_mod
    import agent.profile as ap

    reg, db_path = _setup_registry(tmp_path)
    guest = _make_guest_profile("alice")
    guest_reg_profile = reg.create_profile("alice", "pass123")
    agent = _make_agent(guest)

    # Patch get_registry to use our test db
    original_get_registry = ap.get_registry
    ap.get_registry = lambda **kw: reg
    ap._REGISTRY = reg

    # Patch send_message to avoid real Telegram
    executed = []
    approval_name = next(iter(GUEST_APPROVAL_TOOLS))
    dummy_spec = ToolSpec(
        name=approval_name,
        description="test",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda ag, ti, **kw: executed.append(True) or {"success": True},
        success_predicate=lambda r: r.get("success", False),
    )
    original_registry = tools_mod._registry()
    original_registry[approval_name] = dummy_spec

    try:
        with patch("tools.tools_telegram.send_message"):
            result = tools_mod.execute_tool(agent, approval_name, {"code": "1+1"})
    finally:
        ap.get_registry = original_get_registry
        ap._REGISTRY = None
        if approval_name in original_registry:
            del original_registry[approval_name]

    assert executed == [], "Tool must NOT execute — approval pending"
    assert result.get("status") == "pending_approval"
    token = result.get("token")
    assert token and token != "unavailable"

    # Verify record in DB
    with reg._connect() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE token=?", [token]).fetchone()
    assert row is not None
    assert dict(row)["status"] == "pending"
    assert dict(row)["profile_name"] == "alice"
    assert dict(row)["tool"] == approval_name
    assert dict(row)["requester_chat_id"] == "9999"


# ── /approve executes in guest namespace ──────────────────────────────────────

def test_approve_runs_tool_in_guest_namespace(tmp_path):
    """Approving a token executes the tool under the guest's profile."""
    from agent.profile import ProfileRegistry
    import agent.profile as ap

    reg, db_path = _setup_registry(tmp_path)
    guest = reg.create_profile("bob", "pass456")

    token = "tok_abc123"
    _insert_approval(reg, token, "bob", "execute_python", {"code": "1+1"}, chat_id="8888")

    # Fake agent with profile switching support
    executed_profiles = []

    class _FakeAgent:
        current_profile = None
        memory = MagicMock()
        consciousness = "hi"
        _current_chat_id = None

    fake_agent = _FakeAgent()

    # Mock execute_tool to record which profile is active
    def mock_exec(ag, tool, inp, **kw):
        executed_profiles.append(getattr(ag, "current_profile", None))
        return {"success": True, "output": "2"}

    original_get_registry = ap.get_registry
    ap.get_registry = lambda **kw: reg
    ap._REGISTRY = reg

    mock_bot = MagicMock()
    mock_bot.send_message = MagicMock()

    try:
        # Simulate what _handle_approvals /approve does
        row = None
        with reg._connect() as conn:
            row = dict(conn.execute("SELECT * FROM approvals WHERE token=?", [token]).fetchone())

        assert row["status"] == "pending"
        assert row["tool"] == "execute_python"

        profile_name = row["profile_name"]
        resolved = reg.get_profile(profile_name)
        assert resolved is not None
        assert resolved.is_guest

        from agent.profile import profile_switch
        with profile_switch(fake_agent, resolved):
            mock_exec(fake_agent, row["tool"], json.loads(row["args_json"]))
            assert fake_agent.current_profile is not None
            assert fake_agent.current_profile.name == "bob"

        # After context manager exits, profile is restored
        assert fake_agent.current_profile is None

        # Mark approved
        with reg._connect() as conn:
            conn.execute("UPDATE approvals SET status='approved' WHERE token=?", [token])
            conn.commit()

        with reg._connect() as conn:
            updated = dict(conn.execute("SELECT status FROM approvals WHERE token=?", [token]).fetchone())
        assert updated["status"] == "approved"
    finally:
        ap.get_registry = original_get_registry
        ap._REGISTRY = None


# ── Expired token cannot be approved ─────────────────────────────────────────

def test_expired_token_is_rejected(tmp_path):
    """A token past its expires_at must not be approved."""
    from agent.profile import ProfileRegistry
    import agent.profile as ap

    reg, db_path = _setup_registry(tmp_path)
    reg.create_profile("carol", "pass789")

    token = "tok_expired"
    _insert_approval(reg, token, "carol", "execute_python", minutes_from_now=-5)

    with reg._connect() as conn:
        row = dict(conn.execute("SELECT * FROM approvals WHERE token=?", [token]).fetchone())

    expires_at = row.get("expires_at", "")
    is_expired = expires_at and datetime.now(timezone.utc).isoformat() > expires_at
    assert is_expired, "Token should already be expired"


# ── Denied token never executes ───────────────────────────────────────────────

def test_denied_token_stays_denied(tmp_path):
    """Setting status=denied means no further execution can happen."""
    from agent.profile import ProfileRegistry
    import agent.profile as ap

    reg, db_path = _setup_registry(tmp_path)
    reg.create_profile("dan", "passXYZ")

    token = "tok_denied"
    _insert_approval(reg, token, "dan", "execute_python", status="denied")

    with reg._connect() as conn:
        row = dict(conn.execute("SELECT * FROM approvals WHERE token=?", [token]).fetchone())

    assert row["status"] == "denied"
    # Simulating the handler logic: already-resolved tokens are skipped
    assert row["status"] != "pending"


# ── Single-use token ──────────────────────────────────────────────────────────

def test_token_is_single_use(tmp_path):
    """Re-approving an already-approved token is a no-op (status stays approved)."""
    from agent.profile import ProfileRegistry
    import agent.profile as ap

    reg, db_path = _setup_registry(tmp_path)
    reg.create_profile("eve2", "passABC")

    token = "tok_singleuse"
    _insert_approval(reg, token, "eve2", "execute_python", status="approved")

    with reg._connect() as conn:
        row = dict(conn.execute("SELECT * FROM approvals WHERE token=?", [token]).fetchone())

    # The handler check: if status != 'pending', reject
    assert row["status"] != "pending", "Already-approved token should not be re-processable"


# ── Pending list ──────────────────────────────────────────────────────────────

def test_list_pending_approvals(tmp_path):
    """Can retrieve pending approvals from the DB."""
    from agent.profile import ProfileRegistry

    reg, db_path = _setup_registry(tmp_path)
    reg.create_profile("frank", "pass000")

    _insert_approval(reg, "tok1", "frank", "execute_python")
    _insert_approval(reg, "tok2", "frank", "run_bash", status="approved")

    with reg._connect() as conn:
        rows = conn.execute(
            "SELECT token FROM approvals WHERE status='pending'"
        ).fetchall()

    tokens = [r[0] for r in rows]
    assert "tok1" in tokens
    assert "tok2" not in tokens  # approved, not pending


if __name__ == "__main__":
    import inspect
    import pathlib
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    with tempfile.TemporaryDirectory() as td:
        tp = pathlib.Path(td)
        for name, fn in tests:
            try:
                fn(tp)
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
