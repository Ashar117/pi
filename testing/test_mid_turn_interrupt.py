"""Tests for T-179: mid-turn interrupt (KeyboardInterrupt handling in tool loop)."""
import sys
import os
import types
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_fake_tool_calls(ids):
    """Return a list of fake tool_call-like objects."""
    results = []
    for tc_id in ids:
        tc = types.SimpleNamespace(id=tc_id, name="fake_tool", input={})
        results.append(tc)
    return results


class FakePiAgent:
    """Minimal stub that reproduces the T-179 interrupt logic."""

    def __init__(self):
        self.messages = []
        self.interrupted = False

    def _serialize(self, r):
        return str(r)

    def _execute_tool(self, name, inp, memory_override=None):
        return {"ok": True}

    def run_tool_loop(self, tool_calls, interrupt_at=None):
        """Simulate the T-179 patched tool loop body."""
        tool_results = []
        _interrupted = False
        try:
            for i, tc in enumerate(tool_calls):
                if interrupt_at is not None and i == interrupt_at:
                    raise KeyboardInterrupt
                result = self._execute_tool(tc.name, tc.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": self._serialize(result),
                })
        except KeyboardInterrupt:
            executed_ids = {r["tool_use_id"] for r in tool_results}
            for tc in tool_calls:
                if tc.id not in executed_ids:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": "[cancelled by user]",
                    })
            _interrupted = True

        self.messages.append({"role": "user", "content": tool_results})
        if _interrupted:
            self.messages.append({
                "role": "assistant",
                "content": "[Turn cancelled by user interrupt.]",
            })
        self.interrupted = _interrupted
        return tool_results


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_no_interrupt_completes_normally():
    agent = FakePiAgent()
    tcs = _make_fake_tool_calls(["id-1", "id-2"])
    results = agent.run_tool_loop(tcs, interrupt_at=None)
    assert len(results) == 2
    assert all(r["content"] != "[cancelled by user]" for r in results)
    assert not agent.interrupted


def test_interrupt_at_first_tool_synthesizes_all_cancelled():
    agent = FakePiAgent()
    tcs = _make_fake_tool_calls(["id-1", "id-2", "id-3"])
    results = agent.run_tool_loop(tcs, interrupt_at=0)
    assert len(results) == 3
    assert all(r["content"] == "[cancelled by user]" for r in results)
    assert agent.interrupted


def test_interrupt_mid_loop_partial_completion():
    agent = FakePiAgent()
    tcs = _make_fake_tool_calls(["id-1", "id-2", "id-3"])
    results = agent.run_tool_loop(tcs, interrupt_at=1)
    # id-1 completed, id-2 and id-3 cancelled
    completed = [r for r in results if r["content"] != "[cancelled by user]"]
    cancelled = [r for r in results if r["content"] == "[cancelled by user]"]
    assert len(completed) == 1
    assert len(cancelled) == 2


def test_all_tool_ids_covered_after_interrupt():
    """No orphan tool_use id (each id gets exactly one tool_result)."""
    agent = FakePiAgent()
    ids = ["a", "b", "c", "d"]
    tcs = _make_fake_tool_calls(ids)
    results = agent.run_tool_loop(tcs, interrupt_at=2)
    result_ids = [r["tool_use_id"] for r in results]
    assert sorted(result_ids) == sorted(ids)


def test_messages_list_has_user_then_assistant_on_interrupt():
    agent = FakePiAgent()
    tcs = _make_fake_tool_calls(["id-1"])
    agent.run_tool_loop(tcs, interrupt_at=0)
    assert len(agent.messages) == 2
    assert agent.messages[0]["role"] == "user"
    assert agent.messages[1]["role"] == "assistant"
    assert "cancelled" in agent.messages[1]["content"].lower()


def test_messages_list_has_only_user_on_no_interrupt():
    agent = FakePiAgent()
    tcs = _make_fake_tool_calls(["id-1"])
    agent.run_tool_loop(tcs, interrupt_at=None)
    assert len(agent.messages) == 1
    assert agent.messages[0]["role"] == "user"


def test_empty_tool_calls_no_interrupt():
    agent = FakePiAgent()
    results = agent.run_tool_loop([], interrupt_at=None)
    assert results == []
    assert not agent.interrupted


def test_cancelled_content_literal_string():
    agent = FakePiAgent()
    tcs = _make_fake_tool_calls(["only"])
    results = agent.run_tool_loop(tcs, interrupt_at=0)
    assert results[0]["content"] == "[cancelled by user]"


def test_interrupt_flag_false_without_interrupt():
    agent = FakePiAgent()
    tcs = _make_fake_tool_calls(["x"])
    agent.run_tool_loop(tcs, interrupt_at=None)
    assert agent.interrupted is False


def test_single_tool_interrupt_at_last():
    agent = FakePiAgent()
    tcs = _make_fake_tool_calls(["only"])
    # interrupt_at=0 means before executing the first (and only) tool
    results = agent.run_tool_loop(tcs, interrupt_at=0)
    assert len(results) == 1
    assert results[0]["content"] == "[cancelled by user]"
    assert agent.interrupted
