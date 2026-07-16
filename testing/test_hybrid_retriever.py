"""T-292: unified hybrid retriever — proves the paraphrase gap BM25-alone
misses, and that retrieve() (dense + lexical fusion) closes it.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools  # noqa: E402


def _offline_mt(tmp_path):
    return MemoryTools(supabase_url="", supabase_key="",
                        sqlite_path=str(tmp_path / "pi.db"))


def test_retrieve_finds_paraphrase_that_bm25_alone_misses(tmp_path):
    mt = _offline_mt(tmp_path)
    mt.memory_write(content="the lab uses ZEBRAFISH as the model organism",
                     tier="l3", category="note", importance=8)

    # Simulate an embedded L3 row (as T-291's backfill would produce).
    with patch("memory.semantic_dedup.compute_embedding_for_write", return_value=[1.0, 0.0]):
        mt.backfill_l3_embeddings(limit=10)

    paraphrase_query = "which animal is the experiment on now"

    # Ground the gap: lexical-only search finds nothing (zero word overlap,
    # and the small-cache fast-path requires substring containment).
    bm25_only = mt._hybrid_search_l3(paraphrase_query, 10)
    assert bm25_only == [], "test setup invalid: paraphrase must NOT lexically match"

    # retrieve() fuses in dense cosine and must find it.
    with patch("memory.semantic_dedup.get_embedding", return_value=[1.0, 0.0]):
        hits = mt.retrieve(paraphrase_query, k=5)

    assert any("ZEBRAFISH" in h["content"] for h in hits), (
        f"retrieve() should surface the paraphrased fact via dense cosine; got {hits}"
    )
    top = hits[0]
    assert top["tier"] == "l3"
    assert "score" in top


def test_retrieve_degrades_to_lexical_only_without_embedding_provider(tmp_path):
    mt = _offline_mt(tmp_path)
    mt.memory_write(content="the deploy runbook lives in deploy/alibaba/README.md",
                     tier="l3", category="note", importance=6)

    with patch("memory.semantic_dedup.get_embedding", return_value=None):
        hits = mt.retrieve("deploy runbook", k=5)

    assert any("runbook" in h["content"] for h in hits)


def test_retrieve_empty_query_returns_empty(tmp_path):
    mt = _offline_mt(tmp_path)
    assert mt.retrieve("", k=5) == []
    assert mt.retrieve("   ", k=5) == []


# ── T-298: forgetting must survive the dense path ─────────────────────────────

def test_retrieve_does_not_resurrect_expired_rows(tmp_path):
    """An expired (active_until passed) embedded fact must NOT come back via
    dense retrieval — the resurrection bug."""
    from datetime import datetime, timedelta, timezone
    mt = _offline_mt(tmp_path)

    past = datetime.now(timezone.utc) - timedelta(hours=2)
    mt.memory_write(content="the cafe wifi password is FISH123",
                     tier="l3", category="note", importance=8, expiry=past)
    mt.memory_write(content="the lab uses ZEBRAFISH as the model organism",
                     tier="l3", category="note", importance=8)

    with patch("memory.semantic_dedup.compute_embedding_for_write", return_value=[1.0, 0.0]):
        mt.backfill_l3_embeddings(limit=10)

    with patch("memory.semantic_dedup.get_embedding", return_value=[1.0, 0.0]):
        hits = mt.retrieve("what organism do we study", k=5)

    contents = " | ".join(h["content"] for h in hits)
    assert "FISH123" not in contents, f"expired fact resurrected via dense path: {contents}"
    assert "ZEBRAFISH" in contents, "live fact should still be retrievable"


def test_retrieve_ranks_decayed_below_fresh(tmp_path):
    """Equal-importance rows: the one decayed by neglect ranks below the fresh one."""
    import sqlite3 as _sq
    from datetime import datetime, timedelta, timezone
    mt = _offline_mt(tmp_path)

    mt.memory_write(content="the alpha experiment uses zebrafish",
                     tier="l3", category="note", importance=8)
    # different category — same-category word-overlap dedup would skip this write
    mt.memory_write(content="the beta experiment uses zebrafish",
                     tier="l3", category="research_results", importance=8)

    with patch("memory.semantic_dedup.compute_embedding_for_write", return_value=[1.0, 0.0]):
        mt.backfill_l3_embeddings(limit=10)

    # Decay alpha: last touched 100 days ago at a fast decay rate.
    long_ago = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    conn = _sq.connect(mt.sqlite_path)
    conn.execute("UPDATE l3_cache SET last_accessed_at = ?, decay_rate = 0.05 "
                 "WHERE content LIKE '%alpha%'", [long_ago])
    conn.commit()
    conn.close()

    # Paraphrase query → dense-only, so ranking differences come from importance.
    with patch("memory.semantic_dedup.get_embedding", return_value=[1.0, 0.0]):
        hits = mt.retrieve("what organism do we study", k=5)

    order = [h["content"] for h in hits]
    assert any("beta" in c for c in order) and any("alpha" in c for c in order), order
    assert next(i for i, c in enumerate(order) if "beta" in c) < \
           next(i for i, c in enumerate(order) if "alpha" in c), (
        f"decayed row should rank below fresh row: {order}"
    )
