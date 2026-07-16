"""
agent/startup_banner.py — Compact 3-line startup banner (T-041).

Replaces the legacy 12-line init dump with a scannable status line. Ash's
preference: minimal noise, surface only what changes between sessions.

Format:
    Pi v2 · normie · session a3f2e1c · 38 tools
    Telegram offline · Scheduler running · last verify PASS · 12 turns today
    3 reminders due · 2 open tickets · Type 'briefing' for daily, 'help' for commands

Reminder lines (one per due reminder) are appended after the banner.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).parent.parent
_STATUS_PATH = _ROOT / "docs" / "STATUS.md"
_OPEN_TICKETS = _ROOT / "tickets" / "open"
_CLOSED_TICKETS = _ROOT / "tickets" / "closed"
_PUBLIC_DB = _ROOT / "data" / "pi.db"


def _read_verify_status() -> str:
    """Return 'PASS' / 'FAIL' / 'unknown' from docs/STATUS.md."""
    if not _STATUS_PATH.exists():
        return "unknown"
    try:
        for line in _STATUS_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[:10]:
            m = re.match(r"\*\*Overall:\*\*\s*(\w+)", line)
            if m:
                return m.group(1).upper()
    except Exception:
        pass
    return "unknown"


def _count_open_tickets() -> int:
    """Count *.json files in tickets/open/."""
    if not _OPEN_TICKETS.exists():
        return 0
    try:
        return len(list(_OPEN_TICKETS.glob("*.json")))
    except Exception:
        return 0


def _format_continuation_line(mode: str) -> str:
    """Build 'where we left off' line from L3 session_history + last closed ticket.

    Returns '' silently on any failure (startup must never crash here).
    Format: 'Last session 2026-05-24 14:32 · "summary text..." · last touched T-099'
    """
    try:
        db_path = _PUBLIC_DB
        if not db_path.exists():
            return ""

        summary_text = ""
        session_date = ""
        try:
            with sqlite3.connect(str(db_path), timeout=1) as con:
                row = con.execute(
                    "SELECT content, created_at FROM l3_cache "
                    "WHERE category='session_history' AND invalid_at IS NULL "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            if row:
                content, created_at = row
                if created_at:
                    session_date = created_at[:16].replace("T", " ")
                # Trim to first 80 chars of content
                snippet = (content or "").strip().replace("\n", " ")
                if len(snippet) > 80:
                    snippet = snippet[:77] + "..."
                summary_text = f'"{snippet}"'
        except Exception:
            pass

        last_ticket = ""
        try:
            if _CLOSED_TICKETS.exists():
                files = list(_CLOSED_TICKETS.glob("*.json"))
                if files:
                    newest = max(files, key=lambda p: p.stat().st_mtime)
                    # Extract T-NNN from filename
                    m = re.match(r"(T-\d+)", newest.name)
                    last_ticket = m.group(1) if m else newest.stem
        except Exception:
            pass

        if not session_date and not last_ticket:
            return ""

        parts = []
        if session_date:
            parts.append(f"Last session {session_date}")
        if summary_text:
            parts.append(summary_text)
        if last_ticket:
            parts.append(f"last touched {last_ticket}")
        return " · ".join(parts)
    except Exception:
        return ""


def format_banner(
    *,
    mode: str,
    session_id: str,
    tool_count: int,
    telegram_online: bool,
    scheduler_running: bool,
    turns_today: int,
    reminders: Optional[List[str]] = None,
) -> str:
    """Build the compact 3-line banner. Pure function — easy to unit-test."""
    line1 = f"Pi v2 · {mode} · session {session_id} · {tool_count} tools"

    tg = "online" if telegram_online else "offline"
    sch = "running" if scheduler_running else "off"
    verify = _read_verify_status()
    line2 = f"Telegram {tg} · Scheduler {sch} · last verify {verify} · {turns_today} turns today"

    open_tix = _count_open_tickets()
    rem_count = len(reminders or [])
    rem_str = f"{rem_count} reminder{'s' if rem_count != 1 else ''} due"
    tix_str = f"{open_tix} open ticket{'s' if open_tix != 1 else ''}"
    line3 = f"{rem_str} · {tix_str} · Type 'briefing' for daily, 'help' for commands"

    # T-082: optional 4th line — memory audit status. Empty when audit hasn't run
    # OR there's nothing worth surfacing.
    audit_str = ""
    try:
        from memory.audit import audit_banner_line
        audit_str = audit_banner_line()
    except Exception:
        pass

    continuation = _format_continuation_line(mode)

    lines_out = [line1, line2, line3]
    if audit_str:
        lines_out.append(audit_str)
    if continuation:
        lines_out.append(continuation)
    out = "\n".join(lines_out)

    if reminders:
        out += "\n\n[Pi] REMINDERS DUE TODAY:"
        for r in reminders:
            out += f"\n  {r}"

    return out + "\n"
