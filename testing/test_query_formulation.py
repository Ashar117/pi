"""
test_query_formulation.py — Phase 5 behavioural test for memory_read query quality.

Verifies that when Claude issues a memory_read tool call in response to a
natural-language recall question, the query is short (≤4 tokens) and contains
at least one keyword that appears in the stored content — not a narrative paraphrase.

@pytest.mark.costly — hits real Claude API (~$0.002/run). Run once per prompt change.
"""
import sys
import os
import json
import uuid
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MARKER_KEYWORD = "oregano"
STORED_CONTENT = f"Ash's subway order: oregano bread, chicken, no sauce, extra veggies. marker_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def root_agent():
    from unittest.mock import patch

    def fake_input(prompt=""):
        return "n"

    with patch("builtins.input", side_effect=fake_input):
        from pi_agent import PiAgent
        agent = PiAgent()
        agent.mode = "root"
    return agent


@pytest.fixture(scope="module")
def stored_marker(root_agent):
    """Write a known entry to memory before running recall tests."""
    result = root_agent.memory.memory_write(
        content=STORED_CONTENT,
        tier="l3",
        importance=7,
        category="preferences",
        session_id=root_agent.session_id,
    )
    assert result.get("verified"), f"Setup write failed: {result}"
    return STORED_CONTENT


class TestQueryFormulation:
    """Claude's memory_read queries must be short, keyword-based, not paraphrases."""

    def test_recall_query_contains_keyword(self, root_agent, stored_marker):
        """Ask in natural language; assert the tool call query hits the stored keyword."""
        # Intercept tool calls
        captured_calls = []
        original_execute = root_agent._execute_tool

        def capturing_execute(tool_name, tool_input):
            if tool_name == "memory_read":
                captured_calls.append(tool_input.get("query", ""))
            return original_execute(tool_name, tool_input)

        root_agent._execute_tool = capturing_execute
        try:
            root_agent.process_input("what did I tell you about my subway order?")
        finally:
            root_agent._execute_tool = original_execute

        assert captured_calls, "Claude never called memory_read for a recall question"

        # At least one query must contain the stored keyword
        keyword_hit = any(MARKER_KEYWORD in q.lower() for q in captured_calls)
        assert keyword_hit, (
            f"No memory_read query contained '{MARKER_KEYWORD}'. "
            f"Queries issued: {captured_calls}"
        )

    def test_recall_query_is_short(self, root_agent, stored_marker):
        """Queries must be ≤4 tokens (keyword-based, not narrative paraphrases)."""
        captured_calls = []
        original_execute = root_agent._execute_tool

        def capturing_execute(tool_name, tool_input):
            if tool_name == "memory_read":
                captured_calls.append(tool_input.get("query", ""))
            return original_execute(tool_name, tool_input)

        root_agent._execute_tool = capturing_execute
        try:
            root_agent.process_input("what's my subway sandwich preference?")
        finally:
            root_agent._execute_tool = original_execute

        assert captured_calls, "Claude never called memory_read"

        # The shortest query should be ≤4 tokens
        shortest = min(captured_calls, key=lambda q: len(q.split()))
        token_count = len(shortest.split())
        assert token_count <= 4, (
            f"Shortest query was {token_count} tokens: '{shortest}'. "
            "Expected short keyword query, not narrative paraphrase."
        )
