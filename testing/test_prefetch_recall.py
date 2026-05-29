"""T-151 — _prefetch_memory uses semantic search first, multi-keyword fallback.

Pre-T-151 prefetch queried only keywords[0] via lexical memory_read. Now it
queries the whole phrase via memory_search_semantic, and on miss falls back to
a merged multi-keyword lexical lookup. Hermetic: memory is faked, no network.
"""
import builtins
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_real_input = builtins.input
builtins.input = lambda *a, **k: "no"

from pi_agent import PiAgent  # noqa: E402


class FakeMem:
    def __init__(self, semantic=None, by_kw=None):
        self.semantic = semantic or []
        self.by_kw = by_kw or {}
        self.semantic_calls = []
        self.read_calls = []

    def memory_search_semantic(self, query, limit=5, threshold=0.5):
        self.semantic_calls.append(query)
        return list(self.semantic)

    def memory_read(self, query="", tier=None, limit=20, current_mode=None):
        self.read_calls.append(query)
        return list(self.by_kw.get(query, []))


_AGENT = None


def _agent(mem):
    global _AGENT
    if _AGENT is None:
        _AGENT = PiAgent()
    _AGENT.memory = mem
    return _AGENT


def test_semantic_used_first_and_keyword_skipped_on_hit():
    mem = FakeMem(semantic=[{"content": "Supabase migration is at stage 3", "tier": "l2"}])
    block = _agent(mem)._prefetch_memory("what's the status of the supabase migration?")
    assert "stage 3" in block
    assert mem.semantic_calls, "semantic search was not attempted"
    # whole phrase, not just the first keyword
    assert "supabase" in mem.semantic_calls[0] and "migration" in mem.semantic_calls[0]
    assert mem.read_calls == [], "lexical fallback fired despite a semantic hit"


def test_fallback_queries_multiple_keywords_on_semantic_miss():
    mem = FakeMem(
        semantic=[],
        by_kw={
            "supabase": [{"content": "SB fact", "tier": "l2"}],
            "project":  [{"content": "PRJ fact", "tier": "l3"}],
            "deadline": [{"content": "DL fact", "tier": "l2"}],
        },
    )
    block = _agent(mem)._prefetch_memory("remind me about the supabase project and the deadline")
    assert len(mem.read_calls) >= 2, f"fallback queried only: {mem.read_calls}"
    assert "SB fact" in block


def test_fallback_dedups_repeated_hits():
    dup = [{"id": "x1", "content": "same row", "tier": "l2"}]
    mem = FakeMem(semantic=[], by_kw={"supabase": dup, "project": dup, "deadline": dup})
    block = _agent(mem)._prefetch_memory("remind me about the supabase project and the deadline")
    assert block.count("same row") == 1, "duplicate memory row not deduped"


def test_non_recall_input_skips_prefetch():
    mem = FakeMem(semantic=[{"content": "should not appear"}])
    block = _agent(mem)._prefetch_memory("the weather is nice today and I feel good")
    assert block == "", "prefetch fired on a non-recall statement"
    assert mem.semantic_calls == []


def teardown_module(module):
    builtins.input = _real_input
