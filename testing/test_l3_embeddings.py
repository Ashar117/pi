"""T-291: L3 embeddings — additive column + backfill (offline)."""
import os
import sys
import sqlite3
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools  # noqa: E402


def _offline_mt(tmp_path):
    return MemoryTools(supabase_url="", supabase_key="",
                        sqlite_path=str(tmp_path / "pi.db"))


def test_l3_write_leaves_embedding_null(tmp_path):
    mt = _offline_mt(tmp_path)
    mt.memory_write(content="the lab uses ZEBRAFISH as the model organism",
                     tier="l3", category="note", importance=8)

    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute("SELECT embedding FROM l3_cache WHERE content LIKE '%ZEBRAFISH%'").fetchone()
    conn.close()

    assert row is not None
    assert row[0] is None


def test_backfill_fills_null_embeddings(tmp_path):
    mt = _offline_mt(tmp_path)
    mt.memory_write(content="the lab uses ZEBRAFISH as the model organism",
                     tier="l3", category="note", importance=8)

    with patch("memory.semantic_dedup.compute_embedding_for_write", return_value=[0.1, 0.2, 0.3]):
        updated = mt.backfill_l3_embeddings(limit=10)

    assert updated == 1
    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute("SELECT embedding FROM l3_cache WHERE content LIKE '%ZEBRAFISH%'").fetchone()
    conn.close()
    assert row[0] == "[0.1, 0.2, 0.3]"


def test_session_exit_caretaker_runs_backfill(tmp_path):
    """T-291 wiring: _do_caretaker_full must invoke backfill_l3_embeddings."""
    from unittest.mock import MagicMock
    from agent.session import _do_caretaker_full

    agent = MagicMock()
    agent.memory.sqlite_path = str(tmp_path / "pi.db")
    agent.memory.backfill_l3_embeddings.return_value = 0

    with patch("agent.caretaker.full"):
        _do_caretaker_full(agent)

    agent.memory.backfill_l3_embeddings.assert_called_once()


def test_backfill_skips_rows_already_embedded(tmp_path):
    mt = _offline_mt(tmp_path)
    mt.memory_write(content="fact one", tier="l3", category="note", importance=5)

    with patch("memory.semantic_dedup.compute_embedding_for_write", return_value=[1.0]):
        first = mt.backfill_l3_embeddings(limit=10)
        second = mt.backfill_l3_embeddings(limit=10)

    assert first == 1
    assert second == 0
