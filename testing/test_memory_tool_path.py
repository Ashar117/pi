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
    """Write the marker directly to Supabase L2, bypassing the memory_write test-guard.

    memory_write skips real Supabase writes when PYTEST_CURRENT_TEST is set (to avoid
    polluting production DB during unit tests). This costly integration test needs a
    real L2 row, so we insert directly via the Supabase client instead.
    """
    import uuid as _uuid
    from datetime import datetime, timezone as _tz
    agent = _make_agent()
    entry_id = str(_uuid.uuid4())
    now = datetime.now(_tz.utc).isoformat()
    entry = {
        "id": entry_id,
        "category": "test_t023",
        "title": STORED_CONTENT[:100],
        "content": {
            "text": STORED_CONTENT,
            "metadata": {
                "source": "test",
                "session_id": agent.session_id,
                "created_at_iso": now,
            },
        },
        "importance": 8,
        "status": "active",
        "created_at": now,
    }
    result = agent.memory.supabase.table("organized_memory").insert(entry).execute()
    assert result.data, f"L2 direct insert failed: {result}"
    yield MARKER_KEYWORD
    # cleanup: delete the specific entry by id
    try:
        agent.memory.supabase.table("organized_memory").delete().eq("id", entry_id).execute()
    except Exception:
        pass


class TestMemoryToolPath:
    """Agent must call memory_read (not rely on ambient context) to recall L2 content."""

    def test_memory_read_tool_fires(self, l2_marker):
        """At least one memory_read tool call must fire during L2 recall."""
        agent = _make_agent()

        captured_queries = []
        original_execute = agent._execute_tool

        def capturing_execute(tool_name, tool_input, **kwargs):
            if tool_name == "memory_read":
                captured_queries.append(tool_input.get("query", ""))
            return original_execute(tool_name, tool_input, **kwargs)

        agent._execute_tool = capturing_execute

        agent.process_input(
            f"I stored a secret codeword in your memory. Search your memory for it — it starts with 'zx9'. What is the full codeword?"
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
            f"I stored a secret codeword in your persistent memory that starts with 'zx9'. Search memory and retrieve the full codeword."
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

        def capturing_execute(tool_name, tool_input, **kwargs):
            if tool_name == "memory_read":
                captured_queries.append(tool_input.get("query", ""))
            return original_execute(tool_name, tool_input, **kwargs)

        agent._execute_tool = capturing_execute
        agent.process_input("Search memory for the secret codeword I stored. It starts with zx9.")

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
