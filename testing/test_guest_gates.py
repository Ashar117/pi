"""testing/test_guest_gates.py — T-224: guest capability gates (dispatch-level).

Tests that denied/approval tool sets work at the execute_tool layer.
No real network or file system is written.
"""
import sys
import os
import uuid
import tempfile
import pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock
from agent.profile import Profile, GUEST_DENIED_TOOLS, GUEST_APPROVAL_TOOLS


def _make_guest_profile(name: str = "alice") -> Profile:
    return Profile(
        id=str(uuid.uuid4()),
        name=name,
        display_name=name,
        nickname="",
        password_hash="h",
        salt="s",
        is_guest=True,
        allowlist_json="[]",
        created_at="now",
    )


def _make_ash_profile() -> Profile:
    return Profile(
        id=str(uuid.uuid4()),
        name="ash",
        display_name="ash",
        nickname="",
        password_hash="h",
        salt="s",
        is_guest=False,
        allowlist_json="[]",
        created_at="now",
    )


def _make_agent(profile: Profile) -> MagicMock:
    agent = MagicMock()
    agent.current_profile = profile
    agent.memory = MagicMock()
    agent.evolution = MagicMock()
    agent.evolution.track_pattern = MagicMock()
    return agent


# ── GUEST_DENIED_TOOLS and GUEST_APPROVAL_TOOLS are non-empty ─────────────────

def test_denied_tools_not_empty():
    assert len(GUEST_DENIED_TOOLS) > 0


def test_approval_tools_not_empty():
    assert len(GUEST_APPROVAL_TOOLS) > 0


# ── Guest: denied tool returns structured denial ──────────────────────────────

def test_guest_denied_tool_is_blocked(tmp_path):
    guest = _make_guest_profile()
    agent = _make_agent(guest)

    # Register a dummy ToolSpec for a denied tool name
    from agent.tools import _registry
    from agent.tool_spec import ToolSpec

    denied_name = next(iter(GUEST_DENIED_TOOLS))  # e.g. "modify_file"

    # Patch the registry to have a callable spec for this tool
    original_registry = _registry()
    dummy_spec = ToolSpec(
        name=denied_name,
        description="test",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda ag, ti, **kw: {"success": True, "message": "EXECUTED"},
        success_predicate=lambda r: r.get("success", False),
    )
    original_registry[denied_name] = dummy_spec

    from agent.tools import execute_tool
    result = execute_tool(agent, denied_name, {})

    # Clean up
    if denied_name in original_registry:
        del original_registry[denied_name]

    assert result.get("denied") is True
    assert result.get("success") is False


# ── Guest: approval tool returns pending, does NOT execute ────────────────────

def test_guest_approval_tool_is_queued(tmp_path):
    guest = _make_guest_profile()
    agent = _make_agent(guest)

    approval_name = next(iter(GUEST_APPROVAL_TOOLS))  # e.g. "execute_python"

    # Patch registry
    from agent.tools import _registry
    from agent.tool_spec import ToolSpec

    executed = []
    dummy_spec = ToolSpec(
        name=approval_name,
        description="test",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda ag, ti, **kw: executed.append(True) or {"success": True},
        success_predicate=lambda r: r.get("success", False),
    )
    original_registry = _registry()
    original_registry[approval_name] = dummy_spec

    # Use an in-memory registry path (mock get_registry to avoid real db)
    mock_reg = MagicMock()
    mock_reg._connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_reg._connect.return_value.__exit__ = MagicMock(return_value=False)
    mock_reg._connect.return_value.execute.return_value.close = MagicMock()

    import agent.profile as ap
    original_get_registry = ap.get_registry
    ap.get_registry = lambda **kw: mock_reg

    try:
        from agent.tools import execute_tool
        result = execute_tool(agent, approval_name, {"code": "1+1"})
    finally:
        ap.get_registry = original_get_registry
        if approval_name in original_registry:
            del original_registry[approval_name]

    assert executed == [], "Approval tool must NOT have executed"
    assert result.get("status") == "pending_approval"


# ── Ash (non-guest): tools run normally ──────────────────────────────────────

def test_ash_denied_tool_executes_normally():
    ash = _make_ash_profile()
    agent = _make_agent(ash)

    from agent.tools import _registry
    from agent.tool_spec import ToolSpec

    denied_name = next(iter(GUEST_DENIED_TOOLS))
    executed = []
    dummy_spec = ToolSpec(
        name=denied_name,
        description="test",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda ag, ti, **kw: executed.append(True) or {"success": True},
        success_predicate=lambda r: r.get("success", False),
    )
    original_registry = _registry()
    original_registry[denied_name] = dummy_spec

    try:
        from agent.tools import execute_tool
        result = execute_tool(agent, denied_name, {})
    finally:
        if denied_name in original_registry:
            del original_registry[denied_name]

    assert executed == [True], "Ash should be able to run any tool"
    assert result.get("success") is True


# ── No profile (legacy): tools run normally ───────────────────────────────────

def test_no_profile_executes_normally():
    agent = MagicMock()
    agent.current_profile = None
    agent.memory = MagicMock()
    agent.evolution = MagicMock()
    agent.evolution.track_pattern = MagicMock()

    from agent.tools import _registry
    from agent.tool_spec import ToolSpec

    denied_name = next(iter(GUEST_DENIED_TOOLS))
    executed = []
    dummy_spec = ToolSpec(
        name=denied_name,
        description="test",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda ag, ti, **kw: executed.append(True) or {"success": True},
        success_predicate=lambda r: r.get("success", False),
    )
    original_registry = _registry()
    original_registry[denied_name] = dummy_spec

    try:
        from agent.tools import execute_tool
        result = execute_tool(agent, denied_name, {})
    finally:
        if denied_name in original_registry:
            del original_registry[denied_name]

    assert executed == [True], "No-profile (legacy) should run tools normally"


# ── Guest CAN use memory_read ─────────────────────────────────────────────────

def test_guest_can_use_memory_read():
    guest = _make_guest_profile()
    agent = _make_agent(guest)

    from agent.tools import _registry
    from agent.tool_spec import ToolSpec

    dummy_spec = ToolSpec(
        name="memory_read",
        description="test",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda ag, ti, **kw: {"success": True, "results": []},
        success_predicate=lambda r: True,
    )
    original_registry = _registry()
    original_registry["memory_read"] = dummy_spec

    try:
        from agent.tools import execute_tool
        result = execute_tool(agent, "memory_read", {"query": "test"})
    finally:
        if "memory_read" in original_registry:
            del original_registry["memory_read"]

    assert result.get("denied") is not True
    assert "pending_approval" not in str(result.get("status", ""))


if __name__ == "__main__":
    import inspect
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as td:
            try:
                sig = inspect.signature(fn)
                params = list(sig.parameters.keys())
                fn(pathlib.Path(td)) if "tmp_path" in params else fn()
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
