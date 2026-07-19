"""T-306: _sync_l3 must not wipe local-only L3 columns on every sync.

Regression guard for a silent-data-loss bug: _sync_l3 used to DELETE FROM
l3_cache then re-INSERT only the 7 columns Supabase's l3_active_memory
carries, resetting embedding/decay_rate/pinned/mode/conversation_id/scope/
etc. to their defaults on every 300s sync -- and unconditionally destroying
kind='derived' rows, which never have a Supabase counterpart at all.
Offline -- a recording double stands in for Supabase; no network needed.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools


class _FakeSupa:
    """Returns a fixed row set for l3_active_memory.select().order().limit().execute()."""
    _mock_name = "fake_supa"

    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        return self

    def select(self, cols):
        return self

    def order(self, col, desc=False):
        return self

    def limit(self, n):
        return self

    def execute(self):
        return type("_R", (), {"data": self._rows})()


def _offline_mt(tmp_path):
    return MemoryTools(supabase_url="", supabase_key="",
                        sqlite_path=str(tmp_path / "pi.db"))


def test_sync_preserves_local_only_columns_for_surviving_row(tmp_path):
    mt = _offline_mt(tmp_path)

    conn = sqlite3.connect(mt.sqlite_path)
    conn.execute("""
        INSERT INTO l3_cache
            (id, content, importance, category, active_until, created_at,
             embedding, decay_rate, pinned, mode, conversation_id, scope)
        VALUES ('row-1', 'old content', 5, 'note', NULL, '2026-01-01T00:00:00Z',
                '[0.1, 0.2]', 0.42, 1, 'root', 'conv-abc', 'T-306')
    """)
    conn.commit()
    conn.close()

    # Supabase reports the row with only its 7 owned columns, content updated.
    mt.supabase = _FakeSupa([{
        "id": "row-1", "content": "new content", "importance": 6,
        "category": "note", "active_until": None,
        "created_at": "2026-01-01T00:00:00Z", "metadata": {},
    }])

    mt._sync_l3()

    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute(
        "SELECT content, importance, embedding, decay_rate, pinned, mode, "
        "conversation_id, scope FROM l3_cache WHERE id = 'row-1'"
    ).fetchone()
    conn.close()

    assert row is not None, "row should survive the sync"
    assert row[0] == "new content", "Supabase-owned column should update"
    assert row[1] == 6
    assert row[2] == "[0.1, 0.2]", "embedding must survive sync"
    assert row[3] == 0.42, "decay_rate must survive sync"
    assert row[4] == 1, "pinned must survive sync"
    assert row[5] == "root", "mode must survive sync"
    assert row[6] == "conv-abc", "conversation_id must survive sync"
    assert row[7] == "T-306", "scope must survive sync"


def test_sync_never_touches_derived_rows(tmp_path):
    mt = _offline_mt(tmp_path)

    conn = sqlite3.connect(mt.sqlite_path)
    conn.execute("""
        INSERT INTO l3_cache (id, content, importance, category, created_at, kind, source_id)
        VALUES ('derived-1', '(pending recompute)', 8, 'derived', '2026-01-01T00:00:00Z',
                'derived', 'row-1')
    """)
    conn.commit()
    conn.close()

    # Supabase knows nothing about derived rows -- empty fetch.
    mt.supabase = _FakeSupa([])

    mt._sync_l3()

    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute("SELECT id, kind FROM l3_cache WHERE id = 'derived-1'").fetchone()
    conn.close()
    assert row is not None, "derived rows have no Supabase counterpart and must survive sync"
    assert row[1] == "derived"


def test_sync_removes_rows_no_longer_in_supabase(tmp_path):
    mt = _offline_mt(tmp_path)

    conn = sqlite3.connect(mt.sqlite_path)
    conn.execute("""
        INSERT INTO l3_cache (id, content, importance, category, created_at)
        VALUES ('gone-1', 'was hard-deleted remotely', 5, 'note', '2026-01-01T00:00:00Z')
    """)
    conn.commit()
    conn.close()

    mt.supabase = _FakeSupa([])  # remote no longer has this row

    mt._sync_l3()

    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute("SELECT id FROM l3_cache WHERE id = 'gone-1'").fetchone()
    conn.close()
    assert row is None, "rows genuinely removed remotely should still disappear locally"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        test_sync_preserves_local_only_columns_for_surviving_row(p)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        test_sync_never_touches_derived_rows(p)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        test_sync_removes_rows_no_longer_in_supabase(p)
    print("OK")
