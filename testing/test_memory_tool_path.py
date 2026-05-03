"""
testing/test_memory_tool_path.py — T-023: force the memory_read tool path.

The Phase 3 canary (test_memory_roundtrip.py) proved that storage and L3 ambient
injection work — but agent #2 recalled the marker from the system prompt, not by
calling memory_read. This test forces the tool path:

  1. Write the marker ONLY to L2 (organized_memory in Supabase).
     L2 is never loaded into get_l3_context(), so the agent cannot see it
     from the system prompt — it MUST call memory_read to retrieve it.
  2. Ask agent #2 to recall the marker in a fresh session.
  3. Assert that at least one memory_read tool call fired during that response.
  4. Assert the recall response contains the stored keyword.

If this test goes red, Claude is formulating bad queries (or not calling
memory_read at all), which is the production failure mode from LOG1/LOG2.

@pytest.mark.costly — hits Supabase + Claude API (~$0.002/run).
Run once per consciousness.txt change; do not add to the free regression suite.
"""
import sys
import os
import uuid
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MARKER_KEYWORD = f"zx9marker{uuid.uuid4().hex[:6]}"
STORED_CONTENT = (
    f"Ash's lucky number is 42. The secret codeword for this test is {MARKER_KEYWORD}."
)


def _fake_input(_prompt=""):
    return "n"


def _make_agent():
    with patch("builtins.input", side_effect=_fake_input):
        from pi_agent import PiAgent
        agent = PiAgent()
        agent.mode = "root"
    return agent


@pytest.fixture(scope="module")
def l2_marker():
    """Write the marker to L2 only (not L3, so it won't appear in ambient context)."""
    agent = _make_agent()
    result = agent.memory.memory_write(
        content=STORED_CONTENT,
        tier="l2",
        importance=8,
        category="test_t023",
        session_id=agent.session_id,
    )
    assert result.get("success"), f"L2 setup write failed: {result}"
    yield MARKER_KEYWORD
    # cleanup: best-effort delete from organized_memory
    try:
        agent.memory.supabase.table("organized_memory")\
            .delete()\
            .ilike("content->>text", f"%{MARKER_KEYWORD}%")\
            .execute()
    except Exception:
        pass


class TestMemoryToolPath:
    """Agent must call memory_read (not rely on ambient context) to recall L2 content."""

    def test_memory_read_tool_fires(self, l2_marker):
        """At least one memory_read tool call must fire during L2 recall."""
        agent = _make_agent()

        captured_queries = []
        original_execute = agent._execute_tool

        def capturing_execute(tool_name, tool_input):
            if tool_name == "memory_read":
                captured_queries.append(tool_input.get("query", ""))
            return original_execute(tool_name, tool_input)

        agent._execute_tool = capturing_execute

        agent.process_input(
            f"What is the secret codeword? (hint: it starts with 'zx9')"
        )

        assert captured_queries, (
            "Claude never called memory_read when asked to recall L2-only content. "
            "This is the LOG1/LOG2 production failure mode — the agent answered "
            "(or refused) without ever querying memory."
        )

    def test_recall_response_contains_marker(self, l2_marker):
        """The recall response must contain the stored keyword."""
        agent = _make_agent()

        response = agent.process_input(
            f"I stored a secret codeword that starts with 'zx9'. What was it?"
        )

        assert l2_marker.lower() in response.lower(), (
            f"Recall response did not contain the stored keyword '{l2_marker}'.\n"
            f"Response: {response[:300]}\n"
            "Possible causes: bad query formulation, L2 search miss, or Claude answered "
            "from context rather than memory."
        )

    def test_query_contains_useful_keyword(self, l2_marker):
        """The memory_read query must contain a keyword that could match stored content."""
        agent = _make_agent()

        captured_queries = []
        original_execute = agent._execute_tool

        def capturing_execute(tool_name, tool_input):
            if tool_name == "memory_read":
                captured_queries.append(tool_input.get("query", ""))
            return original_execute(tool_name, tool_input)

        agent._execute_tool = capturing_execute
        agent.process_input("What secret codeword did I store? It starts with zx9.")

        if not captured_queries:
            pytest.skip("No memory_read called — test_memory_read_tool_fires covers this failure")

        useful = any(
            "zx9" in q.lower() or "codeword" in q.lower() or "secret" in q.lower()
            for q in captured_queries
        )
        assert useful, (
            f"No query contained a useful keyword. Queries: {captured_queries}. "
            "Claude is paraphrasing intent instead of using stored keywords."
        )
