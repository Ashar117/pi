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


MARKER_KEYWORD = "subway"
STORED_CONTENT = f"Ash's subway order: oregano bread, chicken, no sauce, extra veggies. marker_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def root_agent():
    from unittest.mock import patch

    def fake_input(prompt=""):
        return "n"

    with patch("builtins.input", side_effect=fake_input):
        from pi_agent import PiAgent
        agent = PiAgent()
        agent.mode = "root"
    return agent


@pytest.fixture()
def stored_marker(root_agent):
    """Write the marker to L2 Supabase; purge stale L3 subway entries first.

    Must be L2 (not L3) so the data is NOT injected into the system prompt —
    Claude must call memory_read to find it. Stale L3 entries from previous
    test runs would contaminate the system prompt and make memory_read unnecessary,
    so we purge them from the test SQLite DB before proceeding.

    memory_write skips real Supabase writes under PYTEST_CURRENT_TEST, so we
    insert directly via the Supabase client.
    """
    import uuid as _uuid
    import sqlite3 as _sqlite3
    from datetime import datetime, timezone as _tz

    # Purge stale "subway" entries from both Supabase l3_active_memory and the
    # local SQLite l3_cache so they don't pollute the system prompt warm segment
    # and short-circuit memory_read calls (L3 is injected into system prompt;
    # if the data is there Claude won't call memory_read).
    try:
        rows = root_agent.memory.supabase.table("l3_active_memory").select("id").ilike("content", "%subway%").execute()
        for row in (rows.data or []):
            root_agent.memory.supabase.table("l3_active_memory").delete().eq("id", row["id"]).execute()
    except Exception:
        pass
    try:
        conn = _sqlite3.connect(root_agent.memory.sqlite_path)
        conn.execute("DELETE FROM l3_cache WHERE content LIKE '%subway%'")
        conn.commit()
        conn.close()
    except Exception:
        pass

    entry_id = str(_uuid.uuid4())
    now = datetime.now(_tz.utc).isoformat()
    entry = {
        "id": entry_id,
        "category": "preferences",
        "title": STORED_CONTENT[:100],
        "content": {
            "text": STORED_CONTENT,
            "metadata": {"source": "test", "session_id": root_agent.session_id, "created_at_iso": now},
        },
        "importance": 7,
        "status": "active",
        "created_at": now,
    }
    result = root_agent.memory.supabase.table("organized_memory").insert(entry).execute()
    assert result.data, f"L2 direct insert failed: {result}"
    yield STORED_CONTENT
    try:
        root_agent.memory.supabase.table("organized_memory").delete().eq("id", entry_id).execute()
    except Exception:
        pass


class TestQueryFormulation:
    """Claude's memory_read queries must be short, keyword-based, not paraphrases."""

    def test_recall_query_contains_keyword(self, root_agent, stored_marker):
        """Ask in natural language; assert the tool call query hits the stored keyword."""
        # Intercept tool calls
        captured_calls = []
        original_execute = root_agent._execute_tool

        def capturing_execute(tool_name, tool_input, **kwargs):
            if tool_name == "memory_read":
                captured_calls.append(tool_input.get("query", ""))
            return original_execute(tool_name, tool_input, **kwargs)

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

        def capturing_execute(tool_name, tool_input, **kwargs):
            if tool_name == "memory_read":
                captured_calls.append(tool_input.get("query", ""))
            return original_execute(tool_name, tool_input, **kwargs)

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
