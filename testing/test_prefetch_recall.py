"""T-293 — _prefetch_memory uses the fused hybrid retriever (retrieve()).

Pre-T-293 prefetch queried memory_search_semantic (L2-only) then fell back to
per-keyword memory_read. Now it calls MemoryTools.retrieve() once, which
fuses dense cosine + lexical across L3+L2 in one ranked call. Hermetic:
memory is faked, no network.
"""
import builtins
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_real_input = builtins.input
builtins.input = lambda *a, **k: "no"

from pi_agent import PiAgent  # noqa: E402


class FakeMem:
    def __init__(self, hits=None):
        self.hits = hits or []
        self.retrieve_calls = []

    def retrieve(self, query, k=6, current_mode=None,
                 current_conversation_id=None, current_scope=None):
        self.retrieve_calls.append(query)
        return list(self.hits)


_AGENT = None


def _agent(mem):
    global _AGENT
    if _AGENT is None:
        _AGENT = PiAgent()
    _AGENT.memory = mem
    return _AGENT


def test_recall_question_calls_retrieve_with_full_query():
    mem = FakeMem(hits=[{"content": "Supabase migration is at stage 3", "tier": "l2"}])
    block = _agent(mem)._prefetch_memory("what's the status of the supabase migration?")
    assert "stage 3" in block
    assert mem.retrieve_calls, "retrieve() was not called"
    assert "supabase" in mem.retrieve_calls[0].lower()


def test_multiple_hits_all_included():
    mem = FakeMem(hits=[
        {"content": "SB fact", "tier": "l2"},
        {"content": "PRJ fact", "tier": "l3"},
    ])
    block = _agent(mem)._prefetch_memory("remind me about the supabase project and the deadline")
    assert "SB fact" in block and "PRJ fact" in block


def test_no_hits_returns_empty():
    mem = FakeMem(hits=[])
    block = _agent(mem)._prefetch_memory("remind me about the supabase project")
    assert block == ""


def test_non_recall_input_skips_prefetch():
    mem = FakeMem(hits=[{"content": "should not appear"}])
    block = _agent(mem)._prefetch_memory("the weather is nice today and I feel good")
    assert block == "", "prefetch fired on a non-recall statement"
    assert mem.retrieve_calls == []


def teardown_module(module):
    builtins.input = _real_input
