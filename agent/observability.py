"""Silent-failure ledger — record-and-forget observability for swallowed exceptions.

API:
  track_silent(category, exc, *, context)  — non-raising insert into SQLite ring
  recent_failures(hours)                   — {category: count} for last N hours
  cleanup_old(max_rows)                    — evict oldest rows above cap; returns deleted count

DB: data/silent_failures.db  (auto-created, capped at 10 000 rows)
Category convention: dot-namespaced, e.g. 'memory.bump_access', 'telegram.handler'
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

from agent.redaction import safe_error

_DB_PATH = Path(__file__).parent.parent / "data" / "silent_failures.db"
_MAX_ROWS = 10_000
_CLEANUP_EVERY = 100  # check row count every N inserts

_DDL = """
CREATE TABLE IF NOT EXISTS silent_failures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    category        TEXT    NOT NULL,
    exception_type  TEXT    NOT NULL,
    redacted_message TEXT,
    context_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_failures_cat_time
    ON silent_failures (category, timestamp DESC);
"""

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_insert_count = 0


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        c.executescript(_DDL)
        c.commit()
        _conn = c
    return _conn


def track_silent(
    category: str,
    exc: Optional[Exception] = None,
    *,
    context: Optional[Dict] = None,
) -> None:
    """Record a silent failure. Never raises — any internal error is swallowed."""
    global _insert_count
    try:
        exc_type = type(exc).__name__ if exc is not None else "unknown"
        redacted = safe_error(exc, audience="public_log") if exc is not None else None
        ctx_json = json.dumps(context) if context else None
        ts = datetime.now(timezone.utc).isoformat()

        with _lock:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO silent_failures "
                "(timestamp, category, exception_type, redacted_message, context_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, category, exc_type, redacted, ctx_json),
            )
            conn.commit()
            _insert_count += 1
            if _insert_count % _CLEANUP_EVERY == 0:
                cleanup_old(_MAX_ROWS)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        pass  # intentional — track_silent must never raise


def recent_failures(hours: int = 24) -> Dict[str, int]:
    """Return {category: count} for failures in the last *hours* hours."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with _lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT category, COUNT(*) FROM silent_failures "
                "WHERE timestamp >= ? GROUP BY category",
                (cutoff,),
            ).fetchall()
        return {row[0]: row[1] for row in rows}
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return {}


def cleanup_old(max_rows: int = _MAX_ROWS) -> int:
    """Delete oldest rows so total stays at or below max_rows. Returns deleted count."""
    try:
        with _lock:
            conn = _get_conn()
            (total,) = conn.execute("SELECT COUNT(*) FROM silent_failures").fetchone()
            if total <= max_rows:
                return 0
            excess = total - max_rows
            conn.execute(
                "DELETE FROM silent_failures WHERE id IN "
                "(SELECT id FROM silent_failures ORDER BY id ASC LIMIT ?)",
                (excess,),
            )
            conn.commit()
            return excess
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return 0
