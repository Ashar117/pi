"""memory/archive.py — T-309: move forgotten L3 rows to l3_archive instead of
deleting or flagging them in place.

Shared by agent/retention.py (decay-archive) and tools/tools_memory.py
(prune_l3_expired) so both write the identical row shape — one archive-copy
implementation, not two that can drift when l3_cache gains a column later.

Zero-influence-by-construction: l3_archive is a separate table. No production
read path (retrieve(), BM25/_hybrid_search_l3, get_l3_context ambient
injection, dedup/contradiction gates) ever queries it — only the forgotten
ledger and an explicit restore read from it. A row moved here cannot leak
back into Pi's reasoning via a missed WHERE-clause filter, because nothing
that reasons ever looks at this table.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional


def ensure_l3_archive_table(conn: sqlite3.Connection) -> None:
    """Idempotent: create l3_archive mirroring l3_cache's current columns."""
    cache_cols = conn.execute("PRAGMA table_info(l3_cache)").fetchall()
    if not cache_cols:
        return  # l3_cache doesn't exist yet — nothing to mirror
    col_defs = ", ".join(f'"{c[1]}" {c[2] or "TEXT"}' for c in cache_cols if c[1] != "id")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS l3_archive (
            id TEXT PRIMARY KEY,
            {col_defs},
            archived_at TEXT,
            archive_reason TEXT
        )
    """)


def archive_l3_row(
    conn: sqlite3.Connection,
    row_id: str,
    reason: str,
    now_iso: Optional[str] = None,
) -> bool:
    """Move one row from l3_cache to l3_archive (copy, then delete).

    Idempotent no-op (returns False) if the row is already gone. Does not
    commit — caller controls the transaction boundary.
    """
    ensure_l3_archive_table(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(l3_cache)").fetchall()]
    col_list = ", ".join(f'"{c}"' for c in cols)
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()

    cur = conn.execute(
        f'INSERT OR REPLACE INTO l3_archive ({col_list}, archived_at, archive_reason) '
        f'SELECT {col_list}, ?, ? FROM l3_cache WHERE id = ?',
        [now_iso, reason, row_id],
    )
    if cur.rowcount == 0:
        return False
    conn.execute("DELETE FROM l3_cache WHERE id = ?", [row_id])
    return True


if __name__ == "__main__":
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE l3_cache (id TEXT PRIMARY KEY, content TEXT, importance INTEGER)")
    conn.execute("INSERT INTO l3_cache VALUES ('a', 'hello', 5)")
    assert archive_l3_row(conn, "a", "decay") is True
    assert conn.execute("SELECT id FROM l3_cache WHERE id='a'").fetchone() is None
    row = conn.execute(
        "SELECT content, importance, archive_reason FROM l3_archive WHERE id='a'"
    ).fetchone()
    assert row == ("hello", 5, "decay")
    assert archive_l3_row(conn, "a", "decay") is False, "second call on gone row is a no-op"
    print("OK")
