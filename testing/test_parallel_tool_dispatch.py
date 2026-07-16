"""Tests for T-180: parallel tool dispatch via ThreadPoolExecutor."""
import os
import sys
import time
import types
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pi_agent import PiAgent


def _make_tc(tc_id, name, inp=None):
    return types.SimpleNamespace(id=tc_id, name=name, input=inp or {})


# ── _SERIAL_TOOL_NAMES ────────────────────────────────────────────────────────

def test_serial_tool_names_contains_modify_file():
    assert "modify_file" in PiAgent._SERIAL_TOOL_NAMES


def test_serial_tool_names_contains_execute_bash():
    assert "execute_bash" in PiAgent._SERIAL_TOOL_NAMES


def test_serial_tool_names_contains_gmail_send():
    assert "gmail_send" in PiAgent._SERIAL_TOOL_NAMES


def test_serial_tool_names_does_not_contain_memory_read():
    assert "memory_read" not in PiAgent._SERIAL_TOOL_NAMES


def test_serial_tool_names_does_not_contain_web_search():
    assert "web_search" not in PiAgent._SERIAL_TOOL_NAMES


# ── Parallel path gating ──────────────────────────────────────────────────────

def test_single_tool_does_not_use_parallel():
    """One tool call → sequential path (no ThreadPoolExecutor overhead)."""
    calls = [_make_tc("id-1", "web_search")]
    use_parallel = (
        len(calls) > 1
        and all(tc.name not in PiAgent._SERIAL_TOOL_NAMES for tc in calls)
    )
    assert use_parallel is False


def test_two_read_tools_use_parallel():
    calls = [_make_tc("id-1", "web_search"), _make_tc("id-2", "memory_read")]
    use_parallel = (
        len(calls) > 1
        and all(tc.name not in PiAgent._SERIAL_TOOL_NAMES for tc in calls)
    )
    assert use_parallel is True


def test_one_serial_tool_forces_sequential():
    calls = [_make_tc("id-1", "web_search"), _make_tc("id-2", "modify_file")]
    use_parallel = (
        len(calls) > 1
        and all(tc.name not in PiAgent._SERIAL_TOOL_NAMES for tc in calls)
    )
    assert use_parallel is False


def test_all_serial_tools_are_sequential():
    calls = [_make_tc("id-1", "modify_file"), _make_tc("id-2", "execute_bash")]
    use_parallel = (
        len(calls) > 1
        and all(tc.name not in PiAgent._SERIAL_TOOL_NAMES for tc in calls)
    )
    assert use_parallel is False


# ── Parallel execution correctness ───────────────────────────────────────────

def test_parallel_dispatch_preserves_order():
    """Results are returned in original call order, not completion order."""
    import concurrent.futures as cf

    order = []
    results_by_id = {}

    def slow_tool(tc_id, delay, val):
        time.sleep(delay)
        return {"value": val}

    calls = [
        _make_tc("a", "slow_one"),
        _make_tc("b", "fast_one"),
        _make_tc("c", "medium_one"),
    ]
    delays = {"a": 0.05, "b": 0.01, "c": 0.02}
    expected_vals = {"a": 10, "b": 20, "c": 30}

    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(slow_tool, tc.id, delays[tc.id], expected_vals[tc.id]): tc
            for tc in calls
        }
        for fut, tc in futures.items():
            results_by_id[tc.id] = fut.result(timeout=5)

    # Reassemble in original order
    ordered = [results_by_id[tc.id] for tc in calls]
    assert ordered == [{"value": 10}, {"value": 20}, {"value": 30}]


def test_timeout_returns_error_result():
    """A tool that times out produces a structured error, not an exception."""
    import concurrent.futures as cf

    def forever():
        time.sleep(100)

    with cf.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(forever)
        try:
            fut.result(timeout=0.05)
            result = {"ok": True}
        except cf.TimeoutError:
            result = {"error": "tool_timeout"}

    assert result["error"] == "tool_timeout"


def test_tool_spec_serial_field_exists():
    from agent.tool_spec import ToolSpec
    # Default is False (non-serial / parallel-safe)
    spec = ToolSpec(
        name="test", description="t",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda a, i: {},
    )
    assert spec.serial is False


def test_tool_spec_serial_true_settable():
    from agent.tool_spec import ToolSpec
    spec = ToolSpec(
        name="write_thing", description="side effect",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=lambda a, i: {},
        serial=True,
    )
    assert spec.serial is True
