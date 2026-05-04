"""
testing/test_query_formulation_v2.py — T-027: memory_read query quality.

Three failure patterns from the real chat session:
  1. Statements of fact trigger a spurious memory_read ("followed" searched when
     the user just said "i followed up all good rn" — no question, no recall).
  2. Wrong keyword extracted — "deadlines" (plural) misses stored "deadline" entry;
     "location" searched instead of a proper noun like the city name.
  3. Common/filler words used as queries — "planning", "things", "rn", "atm".

@pytest.mark.costly — hits real Claude API (~$0.003/run). Run once per prompt change.
"""
import sys
import os
import uuid
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEADLINE_CONTENT  = f"Research deadline: March 15 2026. marker_{uuid.uuid4().hex[:8]}"
SUBWAY_CONTENT    = f"User's subway order: oregano bread, chicken, no sauce, extra veggies. marker_{uuid.uuid4().hex[:8]}"
LOCATION_CONTENT  = f"User currently lives in Springfield. marker_{uuid.uuid4().hex[:8]}"


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
def seeded_agent(root_agent):
    """Write known facts to L3 before running recall tests."""
    for content in (DEADLINE_CONTENT, SUBWAY_CONTENT, LOCATION_CONTENT):
        root_agent.memory.memory_write(
            content=content, tier="l3", importance=7,
            category="permanent_profile", session_id=root_agent.session_id,
        )
    return root_agent


def _capture_memory_reads(agent, user_input):
    """Process user_input and return list of every memory_read query issued.

    Patches agent.memory.memory_read directly so both the _prefetch_memory path
    and the Claude tool_use path are captured (they go through different code routes).
    """
    captured = []
    original = agent.memory.memory_read

    def intercepting(query="", **kwargs):
        captured.append(query.lower())
        return original(query=query, **kwargs)

    agent.memory.memory_read = intercepting
    try:
        agent.process_input(user_input)
    finally:
        agent.memory.memory_read = original
    return captured


# ── T-027 test 1: statements must NOT trigger memory_read ─────────────────────

@pytest.mark.costly
def test_statement_of_fact_no_memory_read(seeded_agent):
    """'I followed up all good rn' is a statement — no memory search needed."""
    queries = _capture_memory_reads(seeded_agent, "i followed up all good rn")
    assert queries == [], (
        f"Statement of fact triggered spurious memory_read. Queries: {queries}"
    )


# ── T-027 test 2: plural/singular — "deadlines" must find "deadline" ──────────

@pytest.mark.costly
def test_deadline_query_hits_stored_fact(seeded_agent):
    """'What deadlines do I have' must issue a query that matches stored 'deadline'."""
    queries = _capture_memory_reads(seeded_agent, "what deadlines do i have atm?")

    assert queries, "No memory_read issued for a direct recall question"

    good_keywords = {"deadline", "research", "march"}
    hit = any(any(kw in q for kw in good_keywords) for q in queries)
    assert hit, (
        f"No query contained a useful keyword from {good_keywords}. "
        f"Queries issued: {queries}"
    )


# ── T-027 test 3: proper-noun preference over common nouns ───────────────────

@pytest.mark.costly
def test_location_query_avoids_bare_common_noun(seeded_agent):
    """'My location rn duh' must NOT use 'location' alone as the query keyword."""
    queries = _capture_memory_reads(seeded_agent, "my location rn duh")

    # If a search happened, the query must be more specific than bare "location"
    for q in queries:
        assert q.strip() != "location", (
            f"Query was bare 'location' — too broad to return useful results. "
            f"Should prefer the city name, 'live', or similar. Queries: {queries}"
        )


# ── T-027 test 4: filler words must not be queries ───────────────────────────

@pytest.mark.costly
def test_filler_words_not_used_as_queries(seeded_agent):
    """Common filler words must never appear as the sole memory_read query."""
    FILLER = {"planning", "things", "stuff", "followed", "rn", "atm", "duh",
              "okay", "yeah", "sure", "going", "good", "great"}

    queries = _capture_memory_reads(seeded_agent, "im planning to go to pak in a few weeks")

    for q in queries:
        assert q.strip() not in FILLER, (
            f"Query {q!r} is a filler word — no useful result would come back. "
            f"All queries: {queries}"
        )
