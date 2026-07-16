"""
agent/turn_log.py — Local, durable per-turn log.

Writes one JSON line per conversation turn (both modes) to ``logs/turns.jsonl``.
Independent of Supabase L1 — works offline, never silently drops a turn.

Schema (one entry per turn):
    {
        "turn_id":          str  (uuid4),
        "session_id":       str,
        "ts":               ISO-8601 UTC,
        "mode":             "normie" | "root" | "research",
        "user_input":       str (full),
        "response_preview": str (first 400 chars of response),
        "response_chars":   int,
        "tools_used":       [tool name strings],
        "cost":             float (USD, 0 for free models),
        "duration_ms":      int,
        "tokens_in":        int,
        "tokens_out":       int,
        "model":            str,
        "error":            str | null,
    }

Best-effort: any I/O exception is captured and printed but never raised, so a
disk-full or permission error never breaks the conversation flow.

T-110: recent_turns() uses a tail-stream helper (O(chunk) not O(file)).
       count_today() reads a per-day SQLite counter — O(1).
       append_turn() increments the counter table after writing the jsonl line.
"""

import gzip
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from agent.observability import track_silent

_ROOT = Path(__file__).parent.parent
_LOG_PATH = _ROOT / "logs" / "turns.jsonl"
_ARCHIVE_DIR = _ROOT / "logs" / "archive"
_COUNTS_DB = _ROOT / "data" / "turn_counts.db"

# Maximum bytes to read from end of file when tail-streaming.
_TAIL_MAX_BYTES = 5_000_000


# ── Counter table ─────────────────────────────────────────────────────────────

def _counts_conn() -> sqlite3.Connection:
    _COUNTS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_COUNTS_DB))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS turn_counts "
        "(date TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    return conn


def _increment_counter(today: str) -> None:
    """Increment today's turn counter. Silently swallows failures (soft stat)."""
    try:
        conn = _counts_conn()
        conn.execute(
            "INSERT INTO turn_counts(date, count) VALUES(?, 1) "
            "ON CONFLICT(date) DO UPDATE SET count = count + 1",
            (today,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        track_silent("turn_log.counter_increment", e)


def _bootstrap_counter_if_empty(today: str) -> None:
    """One-time scan of live file to seed today's count when table is empty."""
    try:
        conn = _counts_conn()
        row = conn.execute("SELECT SUM(count) FROM turn_counts").fetchone()
        total = row[0] or 0
        conn.close()
        if total > 0:
            return
        # table is empty — scan live file once to seed today
        count = 0
        if _LOG_PATH.exists():
            with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", "").startswith(today):
                            count += 1
                    except json.JSONDecodeError:
                        continue
        if count:
            conn = _counts_conn()
            conn.execute(
                "INSERT INTO turn_counts(date, count) VALUES(?, ?) "
                "ON CONFLICT(date) DO UPDATE SET count = ?",
                (today, count, count),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        track_silent("turn_log.counter_bootstrap", e)


# ── Tail-stream helpers ───────────────────────────────────────────────────────

def _tail_jsonl(path: Path, n: int, max_bytes: int = _TAIL_MAX_BYTES) -> List[dict]:
    """Read the last n JSON lines from a plaintext jsonl file.

    Reads at most max_bytes from the end of the file — never loads the whole
    file. Drops a partial first line that falls at the chunk boundary.
    """
    if not path.exists():
        return []
    size = path.stat().st_size
    read_size = min(size, max_bytes)
    with open(path, "rb") as f:
        f.seek(max(0, size - read_size))
        raw = f.read()

    lines = raw.split(b"\n")
    # If we didn't read from the start, the first chunk may be a partial line
    if read_size < size:
        lines = lines[1:]

    out: List[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            track_silent("turn_log.parse_error", ValueError(f"unparseable line in {path.name}"))
            continue
        if len(out) >= n:
            break
    return list(reversed(out))


def _read_gz_jsonl(path: Path) -> List[dict]:
    """Decompress and parse a .jsonl.gz archive. Returns all records."""
    out: List[dict] = []
    try:
        with gzip.open(str(path), "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
    except Exception as e:
        track_silent("turn_log.gz_read_error", e)
    return out


# ── Public API ────────────────────────────────────────────────────────────────

def append_turn(
    *,
    session_id: str,
    mode: str,
    user_input: str,
    response: str,
    duration_ms: int,
    tools_used: Optional[List[str]] = None,
    cost: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    model: str = "",
    error: Optional[str] = None,
    profile_name: Optional[str] = None,  # T-226: guest turns route to per-profile log
) -> Optional[str]:
    """Append one turn to logs/turns.jsonl (Ash) or logs/profiles/<name>/turns.jsonl (guest).

    Also increments the per-day counter table (T-110). Never raises.
    """
    turn_id = uuid.uuid4().hex
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    entry: dict = {
        "turn_id":          turn_id,
        "session_id":       session_id,
        "ts":               datetime.now(timezone.utc).isoformat(),
        "mode":             mode,
        "user_input":       user_input,
        "response_preview": (response or "")[:400],
        "response_chars":   len(response or ""),
        "tools_used":       list(tools_used or []),
        "cost":             round(float(cost), 6),
        "duration_ms":      int(duration_ms),
        "tokens_in":        int(tokens_in),
        "tokens_out":       int(tokens_out),
        "model":            model,
        "error":            error,
    }

    # T-226: guest turns route to a per-profile log; Ash stays on the main log.
    if profile_name:
        entry["profile"] = profile_name
        log_path_to_use = _ROOT / "logs" / "profiles" / profile_name / "turns.jsonl"
    else:
        log_path_to_use = _LOG_PATH

    try:
        log_path_to_use.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path_to_use, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # Last-resort: never break the agent because we can't write a log.
        print(f"[Pi] turn_log write failed (non-fatal): {e}")
        return None

    # Counter is a soft stat — failure must never block the turn write above.
    try:
        _increment_counter(today)
    except Exception as e:
        track_silent("turn_log.counter_increment", e)
    return turn_id


def count_today(session_id: Optional[str] = None) -> int:
    """Return number of turns logged today.

    T-110: reads from the per-day SQLite counter (O(1)) rather than scanning
    the whole jsonl file. Falls back to full-scan if the counter table is
    empty (first call after deploy).

    If ``session_id`` is given, falls back to the slow path since the counter
    doesn't track per-session counts.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if session_id:
        # Per-session count: slow path (uncommon, only used by some startup banners)
        if not _LOG_PATH.exists():
            return 0
        count = 0
        try:
            with open(_LOG_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not entry.get("ts", "").startswith(today):
                        continue
                    if entry.get("session_id") != session_id:
                        continue
                    count += 1
        except Exception:
            pass
        return count

    # Fast path: counter table
    _bootstrap_counter_if_empty(today)
    try:
        conn = _counts_conn()
        row = conn.execute(
            "SELECT count FROM turn_counts WHERE date = ?", (today,)
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        track_silent("turn_log.count_today", e)
        return 0


def recent_turns(limit: int = 20, session_id: Optional[str] = None) -> List[dict]:
    """Return the last N turns from the log, newest last.

    T-110: tail-streams the live file (O(max_bytes) not O(file)), then
    walks archives newest-first if the live file has fewer than limit records.
    """
    live = _tail_jsonl(_LOG_PATH, limit)

    if session_id:
        live = [e for e in live if e.get("session_id") == session_id]

    if len(live) >= limit:
        return live[-limit:]

    # Need more records — walk archives newest-first
    needed = limit - len(live)
    archive_entries: List[dict] = []

    if _ARCHIVE_DIR.exists():
        archives = sorted(_ARCHIVE_DIR.glob("turns_jsonl-*.jsonl.gz"), reverse=True)
        for arc in archives:
            if needed <= 0:
                break
            records = _read_gz_jsonl(arc)
            if session_id:
                records = [e for e in records if e.get("session_id") == session_id]
            archive_entries = records[-needed:] + archive_entries
            needed -= len(records)

    combined = archive_entries + live
    return combined[-limit:]


def log_path() -> Path:
    """Expose the path so tests can clean it up."""
    return _LOG_PATH


_ROTATE_THRESHOLD_BYTES = 50_000_000  # T-259: rotate once turns.jsonl passes ~50MB


def rotate_turns_log(threshold_bytes: int = _ROTATE_THRESHOLD_BYTES) -> Optional[Path]:
    """T-259: gzip-archive turns.jsonl once it exceeds threshold_bytes, then truncate.

    Archives land in logs/archive/turns_jsonl-<ts>.jsonl.gz — the exact
    pattern recent_turns() already walks via _read_gz_jsonl(), so archived
    history stays queryable. No-op (returns None) below the threshold or if
    the log doesn't exist yet. Never deletes: the live file is truncated to
    empty, not removed, so appends resume immediately.
    """
    if not _LOG_PATH.exists() or _LOG_PATH.stat().st_size < threshold_bytes:
        return None

    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    archive_path = _ARCHIVE_DIR / f"turns_jsonl-{ts}.jsonl.gz"

    with open(_LOG_PATH, "rb") as src, gzip.open(str(archive_path), "wb") as dst:
        dst.write(src.read())

    # Truncate (not delete) the live file so appends resume immediately.
    open(_LOG_PATH, "w").close()

    return archive_path
