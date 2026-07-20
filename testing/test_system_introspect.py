"""
testing/test_system_introspect.py — T-028: system_introspect tool must return
live state, not stale prompt-derived values.

Evidence: Pi described evolution.jsonl as empty, tickets as empty, no monthly
review — all wrong. Root cause: answering from consciousness.txt knowledge
instead of a live read of the system.

Offline — reads local files (evolution.jsonl, tickets/, solutions/), no API calls.
"""
import sys
import os
import json
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def root_agent():
    def fake_input(prompt=""):
        return "n"

    with patch("builtins.input", side_effect=fake_input):
        from pi_agent import PiAgent
        agent = PiAgent()
        agent.mode = "root"
    return agent


# ── tool exists and is callable ───────────────────────────────────────────────

def test_system_introspect_tool_registered(root_agent):
    """system_introspect must be in the tool definitions Claude sees."""
    definitions = root_agent._get_tool_definitions()
    names = [t["name"] for t in definitions]
    assert "system_introspect" in names, (
        f"system_introspect not in tool definitions. Registered: {names}"
    )


def test_system_introspect_does_not_return_unknown_tool(root_agent):
    """Calling system_introspect must not return 'Unknown tool' error."""
    result = root_agent._execute_tool("system_introspect", {})
    assert "error" not in result or "Unknown tool" not in str(result.get("error", "")), (
        f"system_introspect not dispatched: {result}"
    )


# ── result shape ──────────────────────────────────────────────────────────────

def test_system_introspect_has_required_keys(root_agent):
    """Result must contain the keys the plan specifies."""
    result = root_agent._execute_tool("system_introspect", {})
    for key in ("total_interactions", "closed_ticket_count", "open_ticket_count",
                "solution_count", "session_id", "mode", "last_solution_id"):
        assert key in result, (
            f"system_introspect result missing key {key!r}. Got: {list(result.keys())}"
        )


# ── live values ───────────────────────────────────────────────────────────────

def test_system_introspect_total_interactions_nonzero(root_agent):
    """total_interactions must be read from evolution.jsonl — not 0."""
    evolution_log = os.path.join(ROOT, "logs", "evolution.jsonl")
    if not os.path.exists(evolution_log):
        pytest.skip("evolution.jsonl does not exist yet")

    lines = [l for l in open(evolution_log).read().splitlines() if l.strip()]
    expected_count = len(lines)
    if expected_count == 0:
        pytest.skip("evolution.jsonl is empty")

    result = root_agent._execute_tool("system_introspect", {})
    assert result["total_interactions"] == expected_count, (
        f"total_interactions={result['total_interactions']} but "
        f"evolution.jsonl has {expected_count} lines"
    )


def test_system_introspect_closed_ticket_count_nonzero(root_agent):
    """closed_ticket_count must reflect actual closed/ directory."""
    closed_dir = os.path.join(ROOT, "tickets", "closed")
    if not os.path.isdir(closed_dir):
        pytest.skip("tickets/ is untracked/local-only — not present in this checkout")
    expected = len([f for f in os.listdir(closed_dir) if f.endswith(".json")])
    assert expected > 0, "No closed tickets found — test data issue"

    result = root_agent._execute_tool("system_introspect", {})
    assert result["closed_ticket_count"] == expected, (
        f"closed_ticket_count={result['closed_ticket_count']} but "
        f"tickets/closed/ has {expected} files"
    )


def test_system_introspect_session_id_matches_agent(root_agent):
    """session_id in result must match the running agent's session_id."""
    result = root_agent._execute_tool("system_introspect", {})
    assert result["session_id"] == root_agent.session_id, (
        f"session_id mismatch: tool={result['session_id']!r} "
        f"agent={root_agent.session_id!r}"
    )


def test_system_introspect_solution_count_nonzero(root_agent):
    """solution_count must reflect actual SOLUTIONS.jsonl entries."""
    sol_path = os.path.join(ROOT, "solutions", "SOLUTIONS.jsonl")
    if not os.path.exists(sol_path):
        pytest.skip("SOLUTIONS.jsonl does not exist")

    expected = len([l for l in open(sol_path, encoding="utf-8").read().splitlines() if l.strip()])
    result = root_agent._execute_tool("system_introspect", {})
    assert result["solution_count"] == expected, (
        f"solution_count={result['solution_count']} but SOLUTIONS.jsonl has {expected} lines"
    )
