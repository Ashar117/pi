"""T-137 — context-cued recall: same-mode retrieval boost.

Behind PI_CONTEXT_CUED_RECALL (default off). When on, a fact written in the
current mode gets a small additive score boost, so it outranks an equally-
relevant fact from another mode — without overriding a much-more-relevant
off-mode row. Hermetic: temp SQLite, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools


def _mem(tmp_path, name):
    return MemoryTools(supabase_url="", supabase_key="", db_path=str(tmp_path / name))


def _contents(rows):
    return [r["content"] for r in rows]


# Distinct contents sharing only the rare query token "zephyrproj" so the L3
# word-overlap dedup never merges them (similar phrasing would skip the 2nd row).

def test_boost_favors_current_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CONTEXT_CUED_RECALL", "on")
    m = _mem(tmp_path, "a.db")
    m.memory_write(content="zephyrproj launch is on monday", tier="l3", mode="root")
    m.memory_write(content="zephyrproj budget was approved", tier="l3", mode="normie")

    top_root = m.memory_read(query="zephyrproj", tier="l3", current_mode="root")[0]
    assert "launch" in top_root["content"], "root-mode row not boosted to top"

    top_norm = m.memory_read(query="zephyrproj", tier="l3", current_mode="normie")[0]
    assert "budget" in top_norm["content"], "normie-mode row not boosted to top"


def test_flag_off_disables_boost(tmp_path, monkeypatch):
    monkeypatch.delenv("PI_CONTEXT_CUED_RECALL", raising=False)
    m = _mem(tmp_path, "b.db")
    m.memory_write(content="zephyrproj launch is on monday", tier="l3", mode="root")    # older
    m.memory_write(content="zephyrproj budget was approved", tier="l3", mode="normie")  # newer
    top = m.memory_read(query="zephyrproj", tier="l3", current_mode="root")[0]
    # Boost disabled → recency wins → newer (normie) row on top despite current_mode=root
    assert "budget" in top["content"]


def test_null_mode_rows_no_boost_no_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CONTEXT_CUED_RECALL", "on")
    m = _mem(tmp_path, "c.db")
    m.memory_write(content="deadline alpha", tier="l3")  # mode NULL
    m.memory_write(content="deadline beta", tier="l3")   # mode NULL
    rows = m.memory_read(query="deadline", tier="l3", current_mode="root")
    assert len(rows) >= 1  # no boost, no crash


def test_strong_relevance_still_beats_boost(tmp_path, monkeypatch):
    """A much-more-relevant off-mode row must still surface above a weak same-mode row."""
    monkeypatch.setenv("PI_CONTEXT_CUED_RECALL", "on")
    m = _mem(tmp_path, "d.db")
    # off-mode row strongly matches the query; same-mode row barely mentions it
    m.memory_write(content="the quarterly deadline deadline deadline report",
                   tier="l3", mode="normie", importance=9)
    m.memory_write(content="unrelated note about deadline", tier="l3", mode="root", importance=1)
    top = m.memory_read(query="deadline", tier="l3", current_mode="root")[0]
    assert "quarterly" in top["content"], "small boost wrongly overrode strong relevance"
