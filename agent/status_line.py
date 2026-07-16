"""agent/status_line.py — T-131: per-turn status line repaint (CLI only).

Prints a one-liner to stderr after each CLI response when PI_STATUS_LINE=on.
Never fires in Telegram or voice paths (those never call run()).

Format:
    [root · turn 14 · session a3f2e1c · $0.038 today · 2 open · L3: 184]

ENV: PI_STATUS_LINE=on enables. Default off.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from agent.turn_log import count_today

if TYPE_CHECKING:
    from pi_agent import PiAgent

_ENV_FLAG = "PI_STATUS_LINE"
_ROOT = Path(__file__).parent.parent
_OPEN_TICKETS = _ROOT / "tickets" / "open"


def is_enabled() -> bool:
    return os.environ.get(_ENV_FLAG, "").lower() == "on"


def _count_open_tickets() -> int:
    try:
        return len(list(_OPEN_TICKETS.glob("*.json")))
    except Exception:
        return 0


def _count_l3_rows(agent: "PiAgent") -> int:
    """COUNT(*) active rows in l3_cache. Reads the db path from agent's memory tool."""
    try:
        mem = getattr(agent, "memory", None)
        if mem is None:
            return -1
        db_path = getattr(mem, "sqlite_path", None)
        if db_path is None:
            return -1
        with sqlite3.connect(str(db_path), timeout=1) as con:
            row = con.execute(
                "SELECT COUNT(*) FROM l3_cache WHERE invalid_at IS NULL"
            ).fetchone()
            return row[0] if row else 0
    except Exception:
        return -1


def format_status_line(
    mode: str,
    session_id: str,
    turns_today: int,
    daily_cost: float,
    open_tickets: int,
    l3_rows: int,
) -> str:
    """Pure formatter — all data collected by the caller. Easy to unit-test."""
    sid = session_id
    l3_str = str(l3_rows) if l3_rows >= 0 else "?"
    return (
        f"[{mode} · turn {turns_today} · session {sid} · "
        f"${daily_cost:.3f} today · {open_tickets} open · L3: {l3_str}]"
    )


def emit_if_enabled(agent: "PiAgent") -> None:
    """Collect agent state and print the status line to stderr when enabled.

    Never raises — broken DB or locked SQLite must not surface here.
    """
    if not is_enabled():
        return
    try:
        mode = getattr(agent, "mode", "?")
        session_id = getattr(agent, "session_id", "?")
        turns = count_today()
        daily_cost = 0.0
        try:
            daily_cost = agent.evolution.get_daily_cost()
        except Exception:
            pass
        open_tickets = _count_open_tickets()
        l3_rows = _count_l3_rows(agent)

        line = format_status_line(mode, session_id, turns, daily_cost, open_tickets, l3_rows)
        print(line, file=sys.stderr, flush=True)
    except Exception:
        try:
            print("[Pi · status unavailable]", file=sys.stderr, flush=True)
        except Exception:
            pass
