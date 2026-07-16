"""T-137 / T-142 — L3 records encoding context (mode + conversation_id).

Shared foundation for context-cued recall (T-137, same-mode boost) and
per-conversation scoping (T-142). Hermetic: temp SQLite, no Supabase/network.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools


def _mem(tmp_path, name="t.db"):
    # Empty supabase creds → lazy client never created; L3 (sqlite) is local.
    return MemoryTools(supabase_url="", supabase_key="", db_path=str(tmp_path / name))


def _l3_row(db, like):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT mode, conversation_id FROM l3_cache WHERE content LIKE ?",
            (f"%{like}%",),
        ).fetchone()
    finally:
        conn.close()


def test_l3_write_records_mode_and_conversation_id(tmp_path):
    m = _mem(tmp_path)
    m.memory_write(content="codename is BLUEHERON", tier="l3",
                   mode="root", conversation_id="conv-123")
    assert _l3_row(m.sqlite_path, "BLUEHERON") == ("root", "conv-123")


def test_l3_write_defaults_null_when_context_absent(tmp_path):
    m = _mem(tmp_path, "t2.db")
    m.memory_write(content="a plain fact", tier="l3")
    assert _l3_row(m.sqlite_path, "plain fact") == (None, None)


def test_cross_session_pattern_detected(tmp_path):
    """T-136: an entity recurring across >=3 distinct conversations → pattern."""
    m = _mem(tmp_path, "patterns.db")
    # distinct content per conversation (avoids L3 dedup), shared entity "Zephyr"
    m.memory_write(content="Zephyr launch was discussed", tier="l3", conversation_id="c1")
    m.memory_write(content="reviewed the Zephyr budget", tier="l3", conversation_id="c2")
    m.memory_write(content="Zephyr timeline slipped a week", tier="l3", conversation_id="c3")
    # an entity in only one conversation must NOT pattern
    m.memory_write(content="Quasar is a one-off note", tier="l3", conversation_id="c1")
    patterns = m.detect_cross_session_patterns(min_sessions=3)
    entities = {p["entity"] for p in patterns}
    assert "Zephyr" in entities, f"recurring entity not detected: {patterns}"
    assert "Quasar" not in entities
    z = next(p for p in patterns if p["entity"] == "Zephyr")
    assert z["sessions"] == 3 and z["source"] == "replay" and z["category"] == "pattern_observation"


def test_cross_session_patterns_empty_when_no_conversation_id(tmp_path):
    m = _mem(tmp_path, "nopat.db")
    m.memory_write(content="Zephyr fact without conversation", tier="l3")
    assert m.detect_cross_session_patterns(min_sessions=3) == []


def test_migration_is_idempotent_and_adds_columns(tmp_path):
    db = str(tmp_path / "t3.db")
    MemoryTools(supabase_url="", supabase_key="", db_path=db)
    # Re-init on the same file must not crash (idempotent ALTER guard)
    MemoryTools(supabase_url="", supabase_key="", db_path=db)
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(l3_cache)")}
    conn.close()
    assert "mode" in cols and "conversation_id" in cols
