"""
agent/turn_log.py — Local, durable per-turn log.

Writes one JSON line per conversation turn (both modes) to ``logs/turns.jsonl``.
Independent of Supabase L1 — works offline, never silently drops a turn.

Schema (one entry per turn):
    {
        "turn_id":          str  (uuid4),
        "session_id":       str,
        "ts":               ISO-8601 UTC,
        "mode":             "normie" | "root" | "god" | "research",
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
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).parent.parent
_LOG_PATH = _ROOT / "logs" / "turns.jsonl"


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
) -> Optional[str]:
    """Append one turn to logs/turns.jsonl. Returns the turn_id, or None on failure.

    Never raises. A failure prints a single line and is otherwise silent.
    """
    turn_id = uuid.uuid4().hex

    entry = {
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

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return turn_id
    except Exception as e:
        # Last-resort: never break the agent because we can't write a log.
        print(f"[Pi] turn_log write failed (non-fatal): {e}")
        return None


def count_today(session_id: Optional[str] = None) -> int:
    """Return number of turns logged today. Used by the compact startup banner.

    If ``session_id`` is given, count only that session.
    """
    if not _LOG_PATH.exists():
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
                if session_id and entry.get("session_id") != session_id:
                    continue
                count += 1
    except Exception:
        pass
    return count


def recent_turns(limit: int = 20, session_id: Optional[str] = None) -> List[dict]:
    """Return the last N turns from the log, newest last.

    Used by the sprint runner / retro tools to reconstruct activity.
    """
    if not _LOG_PATH.exists():
        return []

    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
    except Exception:
        return []

    out: List[dict] = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if session_id and entry.get("session_id") != session_id:
            continue
        out.append(entry)
        if len(out) >= limit:
            break

    return list(reversed(out))


def log_path() -> Path:
    """Expose the path so tests can clean it up."""
    return _LOG_PATH
