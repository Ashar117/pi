"""
tools/tools_project.py — Project-level tools for Pi's self-awareness and self-improvement.

Three tools:
  search_codebase  — grep Pi's own source files for symbols / text
  create_ticket    — file a self-improvement ticket from within a session
  get_session_stats — introspect the current session's counters and cost
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

_ROOT = Path(__file__).parent.parent


class ProjectTools:
    """Codebase search, ticket filing, and session introspection for Pi."""

    # ── search_codebase ───────────────────────────────────────────────────────

    def search_codebase(
        self,
        query: str,
        file_pattern: str = "*.py",
        max_results: int = 20,
        context_lines: int = 2,
    ) -> Dict:
        """Search Pi's source files for a regex pattern.

        Args:
            query:         Python regex pattern to search for.
            file_pattern:  Glob to filter files (default: *.py).
            max_results:   Max matching lines to return.
            context_lines: Lines of context before/after each match (0-3).

        Returns:
            {"matches": [...], "count": int, "truncated": bool}
            Each match: {"file": str, "line": int, "text": str, "context": [str]}
        """
        context_lines = min(context_lines, 3)
        matches = []
        truncated = False

        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return {"matches": [], "count": 0, "truncated": False,
                    "error": f"Invalid regex: {e}"}

        skip_dirs = {"pi_env", "__pycache__", ".git", "node_modules", ".pytest_cache"}

        for path in sorted(_ROOT.rglob(file_pattern)):
            # Skip hidden / venv directories
            if any(part in skip_dirs for part in path.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            for i, line in enumerate(lines):
                if pattern.search(line):
                    ctx_start = max(0, i - context_lines)
                    ctx_end = min(len(lines), i + context_lines + 1)
                    context = [
                        f"{'>' if j == i else ' '} {j+1}: {lines[j]}"
                        for j in range(ctx_start, ctx_end)
                    ]
                    matches.append({
                        "file": str(path.relative_to(_ROOT)),
                        "line": i + 1,
                        "text": line.rstrip(),
                        "context": context,
                    })
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break

        return {"matches": matches, "count": len(matches), "truncated": truncated}

    # ── create_ticket ─────────────────────────────────────────────────────────

    def create_ticket(
        self,
        title: str,
        what_failed: str,
        component: str,
        severity: str = "P3",
        where_failed: str = "",
        suggested_fix: str = "",
    ) -> Dict:
        """File a new self-improvement ticket to tickets/open/.

        Auto-generates the next ticket ID by scanning existing tickets.
        Pi should call this whenever it discovers a bug, gap, or improvement
        opportunity during a session.

        Args:
            title:         Short title describing the issue.
            what_failed:   What went wrong / what the gap is.
            component:     File(s) responsible (e.g. "tools/tools_memory.py").
            severity:      P1 | P2 | P3 | P4 (default P3).
            where_failed:  Specific function or location (optional).
            suggested_fix: Implementation hint (optional).

        Returns:
            {"id": "T-NNN", "path": "tickets/open/...", "success": bool}
        """
        ticket_id = self._next_ticket_id()
        now = datetime.now(timezone.utc).isoformat()

        # Sanitize severity
        if severity not in ("P1", "P2", "P3", "P4"):
            severity = "P3"

        ticket = {
            "id": ticket_id,
            "source": "Pi self-report (session tool call)",
            "title": title,
            "component": component,
            "what_failed": what_failed,
            "where_failed": where_failed,
            "why_likely": "",
            "severity": severity,
            "suggested_fix": suggested_fix,
            "status": "open",
            "created": now,
            "closed": None,
            "linked_solution": None,
        }

        slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:60].strip("-")
        filename = f"{ticket_id}-{slug}.json"
        ticket_path = _ROOT / "tickets" / "open" / filename

        try:
            ticket_path.write_text(json.dumps(ticket, indent=2), encoding="utf-8")
            return {"id": ticket_id, "path": f"tickets/open/{filename}", "success": True}
        except OSError as e:
            return {"id": ticket_id, "path": "", "success": False, "error": str(e)}

    def _next_ticket_id(self) -> str:
        """Scan all ticket files and return the next T-NNN id."""
        pattern = re.compile(r"T-(\d+)")
        max_n = 0
        for directory in (_ROOT / "tickets" / "open", _ROOT / "tickets" / "closed"):
            if not directory.exists():
                continue
            for f in directory.iterdir():
                m = pattern.match(f.stem)
                if m:
                    max_n = max(max_n, int(m.group(1)))
        return f"T-{max_n + 1:03d}"

    # ── get_session_stats ─────────────────────────────────────────────────────

    def get_session_stats(self, agent) -> Dict:
        """Return live stats for the current Pi session.

        Args:
            agent: The running PiAgent instance.

        Returns:
            Dict with session_id, mode, turns, uptime_minutes, cost_session,
            cost_today, tokens_in, tokens_out, memory_writes_session.
        """
        now = datetime.now(timezone.utc)
        uptime_minutes = round(
            (now - agent.session_start).total_seconds() / 60, 1
        )

        # Cost / tokens from evolution log for this session
        recent = agent.evolution.get_recent_interactions(hours=24)
        session_interactions = [
            i for i in recent
            if i.get("metadata", {}).get("session_id") == agent.session_id
        ]
        cost_session = sum(i.get("cost", 0) for i in session_interactions)
        tokens_in = sum(i.get("tokens_in", 0) for i in session_interactions)
        tokens_out = sum(i.get("tokens_out", 0) for i in session_interactions)
        cost_today = sum(i.get("cost", 0) for i in recent)

        # Count memory_write tool calls this session
        memory_writes = sum(
            1 for i in session_interactions
            for tc in (i.get("tool_calls") or [])
            if (tc.get("name") or "") == "memory_write"
        )

        return {
            "session_id": agent.session_id,
            "mode": agent.mode,
            "turns": agent.turn_number,
            "uptime_minutes": uptime_minutes,
            "cost_session_usd": round(cost_session, 6),
            "cost_today_usd": round(cost_today, 6),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "memory_writes_session": memory_writes,
        }
