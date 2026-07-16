"""T-270: _sync_l3 must order by created_at desc and cap explicitly.

Regression guard for a silent-truncation bug: an unbounded select("*") on
l3_active_memory let Supabase/PostgREST's implicit ~1000-row cap drop
brand-new writes in arbitrary (non-recency) order once the table grew past
the cap. Offline — a recording double captures the query-builder calls;
no live Supabase needed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools


class _RecordingQuerySupabase:
    """Fluent double capturing table/select/order/limit calls for _sync_l3."""
    _mock_name = "recording_query"

    def __init__(self):
        self.calls = []

    def table(self, name):
        self.calls.append(("table", name))
        return self

    def select(self, cols):
        self.calls.append(("select", cols))
        return self

    def order(self, col, desc=False):
        self.calls.append(("order", col, desc))
        return self

    def limit(self, n):
        self.calls.append(("limit", n))
        return self

    def execute(self):
        return type("_R", (), {"data": []})()


def _offline_mt(tmp_path):
    return MemoryTools(supabase_url="", supabase_key="",
                        sqlite_path=str(tmp_path / "pi.db"))


def test_sync_l3_orders_by_recency_and_caps(tmp_path):
    mt = _offline_mt(tmp_path)
    rec = _RecordingQuerySupabase()
    mt.supabase = rec  # setter stores into _supabase_client; overrides the noop

    mt._sync_l3()

    assert ("table", "l3_active_memory") in rec.calls
    assert ("order", "created_at", True) in rec.calls, (
        f"_sync_l3 did not order by created_at desc: {rec.calls}"
    )
    limit_calls = [c for c in rec.calls if c[0] == "limit"]
    assert limit_calls and limit_calls[0][1] == mt._L3_SYNC_ROW_CAP, (
        f"_sync_l3 did not apply the configured row cap: {rec.calls}"
    )


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        test_sync_l3_orders_by_recency_and_caps(Path(d))
    print("OK")
