"""Tests for T-183: plan-then-execute — PlanState + set_plan/update_plan tools."""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.plan_state import PlanState


# ── PlanState basic operations ────────────────────────────────────────────────

def test_plan_state_starts_empty():
    p = PlanState()
    assert p.is_empty()
    assert len(p) == 0


def test_set_creates_steps():
    p = PlanState()
    p.set(["Step A", "Step B", "Step C"])
    assert len(p) == 3
    assert not p.is_empty()


def test_render_contains_steps():
    p = PlanState()
    p.set(["Read file", "Edit function"])
    rendered = p.render()
    assert "Read file" in rendered
    assert "Edit function" in rendered


def test_render_empty_returns_empty_string():
    p = PlanState()
    assert p.render() == ""


def test_update_changes_status():
    p = PlanState()
    p.set(["Step 1", "Step 2"])
    ok = p.update(0, "done")
    assert ok
    rendered = p.render()
    assert "done" in rendered.lower() or "✓" in rendered


def test_update_out_of_range_returns_false():
    p = PlanState()
    p.set(["Only step"])
    assert p.update(5, "done") is False


def test_update_can_change_text():
    p = PlanState()
    p.set(["Old text"])
    p.update(0, "done", text="New text")
    assert "New text" in p.render()


def test_clear_resets_plan():
    p = PlanState()
    p.set(["A", "B"])
    p.clear()
    assert p.is_empty()
    assert p.render() == ""


def test_render_respects_token_cap():
    p = PlanState()
    p.set([f"Step {i}: " + "x" * 100 for i in range(20)])
    rendered = p.render()
    assert len(rendered) <= 150 * 4 + 10  # max_tokens * 4 + small buffer


def test_set_filters_empty_strings():
    p = PlanState()
    p.set(["Step A", "", "  ", "Step B"])
    assert len(p) == 2


def test_serialise_roundtrip():
    p = PlanState()
    p.set(["Alpha", "Beta"])
    p.update(0, "done")
    d = p.to_dict()
    p2 = PlanState.from_dict(d)
    assert len(p2) == 2
    assert p2._steps[0].status == "done"
    assert p2._steps[1].status == "pending"


# ── Tool handlers ─────────────────────────────────────────────────────────────

def _fake_agent():
    agent = types.SimpleNamespace()
    from agent.plan_state import PlanState
    agent.plan_state = PlanState()
    return agent


def test_set_plan_handler_success():
    from tools.tools_project import _handle_set_plan
    agent = _fake_agent()
    result = _handle_set_plan(agent, {"steps": ["Do X", "Do Y"]})
    assert result["success"] is True
    assert result["steps"] == 2


def test_set_plan_handler_empty_steps():
    from tools.tools_project import _handle_set_plan
    agent = _fake_agent()
    result = _handle_set_plan(agent, {"steps": []})
    assert result["success"] is False


def test_update_plan_handler_success():
    from tools.tools_project import _handle_set_plan, _handle_update_plan
    agent = _fake_agent()
    _handle_set_plan(agent, {"steps": ["Step 0", "Step 1"]})
    result = _handle_update_plan(agent, {"index": 0, "status": "done"})
    assert result["success"] is True


def test_update_plan_handler_no_plan():
    from tools.tools_project import _handle_update_plan
    agent = _fake_agent()
    result = _handle_update_plan(agent, {"index": 0})
    assert result["success"] is False
    assert "no active plan" in result["error"]


def test_update_plan_handler_out_of_range():
    from tools.tools_project import _handle_set_plan, _handle_update_plan
    agent = _fake_agent()
    _handle_set_plan(agent, {"steps": ["Only one"]})
    result = _handle_update_plan(agent, {"index": 99})
    assert result["success"] is False


def test_set_plan_toolspec_registered():
    from tools.tools_project import TOOLS
    names = {t.name for t in TOOLS}
    assert "set_plan" in names
    assert "update_plan" in names
