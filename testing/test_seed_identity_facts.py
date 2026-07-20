"""testing/test_seed_identity_facts.py — T-282."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools
from scripts.seed_identity_facts import seed, IDENTITY_FACTS


def _offline_mt(tmp_path):
    return MemoryTools(supabase_url="", supabase_key="",
                        sqlite_path=str(tmp_path / "pi.db"))


def test_seed_writes_all_facts_at_importance_10(tmp_path):
    # Offline (no Supabase configured), _verify_write now reports success
    # from the SQLite write alone (T-309) — Supabase-less checkouts aren't
    # penalized for lacking a remote tier they were never configured to have.
    mt = _offline_mt(tmp_path)
    results = seed(mt)
    assert len(results) == len(IDENTITY_FACTS)
    assert all(r["result"].get("id") for r in results)
    context = mt.get_l3_context(max_tokens=800)
    for fact in IDENTITY_FACTS:
        assert fact in context


def test_seeded_facts_survive_token_budget_against_trivia(tmp_path):
    """The real bug: importance-10 identity facts must beat importance-5
    trivia under get_l3_context's shared 800-token budget."""
    mt = _offline_mt(tmp_path)
    seed(mt)
    # Simulate the trivia crowding that caused the original failure.
    for i in range(20):
        mt.memory_write(
            content=f"User's order preference number {i}: some food detail padding text here.",
            tier="l3", importance=5, category="permanent_profile", source="stated",
        )

    context = mt.get_l3_context(max_tokens=800)
    for fact in IDENTITY_FACTS:
        assert fact in context, f"identity fact must survive the token budget: {fact!r}"
