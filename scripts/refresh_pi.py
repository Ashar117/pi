#!/usr/bin/env python3
"""
scripts/refresh_pi.py — Regenerate auto-sections of PI.md (T-042).

Replaces only the content between `<!-- BEGIN AUTO §N -->` / `<!-- END AUTO §N -->`
markers. Hand-curated sections (everything else) are never touched.

Sections regenerated:
  §4 — State (phase, last verify, ticket counts, solution count, turns today)
  §7 — Tools inventory (count + grouping from agent.tools.get_tool_definitions)
  §8 — Open tickets (markdown table)
  §9 — Recent solutions (last 10)

Usage:
    python scripts/refresh_pi.py            # regenerate in place
    python scripts/refresh_pi.py --check    # exit 1 if PI.md would change (CI)
    python scripts/refresh_pi.py --dry-run  # print diff to stdout, don't write

Idempotent: running twice yields no diff.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PI_MD = ROOT / "PI.md"
TICKETS_OPEN = ROOT / "tickets" / "open"
TICKETS_CLOSED = ROOT / "tickets" / "closed"
SOLUTIONS = ROOT / "solutions" / "SOLUTIONS.jsonl"
STATUS = ROOT / "docs" / "STATUS.md"
CHECKPOINT = ROOT / "CHECKPOINTS" / "current.md"


# ── Section §4: State ────────────────────────────────────────────────────────

def _verify_summary() -> str:
    """Return e.g. 'PASS · 79 files clean · 26 tests · 0 failures' from STATUS.md."""
    if not STATUS.exists():
        return "unknown"
    txt = STATUS.read_text(encoding="utf-8", errors="replace")

    overall = "unknown"
    m = re.search(r"\*\*Overall:\*\*\s*(\w+)", txt)
    if m:
        overall = m.group(1).upper()

    files_total = files_passed = tests_run = tests_failed = None
    m = re.search(r"Files checked:\s*(\d+)", txt)
    if m:
        files_total = int(m.group(1))
    m = re.search(r"Passed:\s*(\d+)", txt)
    if m:
        files_passed = int(m.group(1))
    m = re.search(r"Tests run:\s*(\d+)", txt)
    if m:
        tests_run = int(m.group(1))
    m = re.search(r"Failures:\s*(\d+)", txt)
    if m:
        tests_failed = int(m.group(1))

    parts = [overall]
    if files_passed is not None and files_total is not None:
        parts.append(f"{files_passed}/{files_total} files clean")
    if tests_run is not None:
        parts.append(f"{tests_run} tests")
    if tests_failed is not None:
        parts.append(f"{tests_failed} failure{'s' if tests_failed != 1 else ''}")
    return " · ".join(parts)


def _phase_from_checkpoint() -> str:
    """Read 'Phase N — title' from CHECKPOINTS/current.md."""
    if not CHECKPOINT.exists():
        return "unknown"
    txt = CHECKPOINT.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\*\*Phase:\*\*\s*([^\n]+)", txt)
    return m.group(1).strip() if m else "unknown"


def _count_files(d: Path, pattern: str = "*.json") -> int:
    if not d.exists():
        return 0
    try:
        return len(list(d.glob(pattern)))
    except Exception:
        return 0


def _solution_count() -> int:
    if not SOLUTIONS.exists():
        return 0
    try:
        with open(SOLUTIONS, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _turns_today() -> int:
    try:
        from agent.turn_log import count_today
        return count_today()
    except Exception:
        return 0


def _last_session_end() -> str:
    """Read 'Last updated: YYYY-MM-DD' from CHECKPOINTS/current.md or fall back to mtime."""
    if not CHECKPOINT.exists():
        return "unknown"
    txt = CHECKPOINT.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\*\*Last updated:\*\*\s*([^\n]+)", txt)
    if m:
        return m.group(1).strip()
    return datetime.fromtimestamp(CHECKPOINT.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def render_section_4() -> str:
    return "\n".join([
        f"- **Phase:** {_phase_from_checkpoint()}",
        f"- **Last verify:** {_verify_summary()}",
        f"- **Open tickets:** {_count_files(TICKETS_OPEN)}",
        f"- **Closed tickets:** {_count_files(TICKETS_CLOSED)}",
        f"- **Solutions logged:** {_solution_count()}",
        f"- **Turns today:** {_turns_today()}",
        f"- **Last session end:** {_last_session_end()}",
    ])


# ── Section §7: Tools inventory ──────────────────────────────────────────────

# Group tools by name pattern. Order is the display order.
_TOOL_GROUPS: List[Tuple[str, List[str]]] = [
    ("Memory", ["memory_read", "memory_write", "memory_delete"]),
    ("Execution", ["execute_python", "execute_bash", "read_file", "modify_file", "create_file"]),
    ("Awareness", ["get_weather", "get_news", "get_stocks", "get_tech_updates", "refresh_awareness"]),
    ("Project", ["search_codebase", "create_ticket", "get_session_stats", "system_introspect"]),
    ("Web", ["web_search", "web_browse", "reddit_browse", "reddit_search", "reddit_thread",
            "scholar_search", "discord_read", "daily_briefing"]),
    ("Obsidian", ["obsidian_read", "obsidian_write", "obsidian_append", "obsidian_search"]),
    ("Image", ["image_gen"]),
    ("Gmail", ["gmail_inbox", "gmail_search", "gmail_read", "gmail_send"]),
    ("Calendar", ["calendar_today", "calendar_upcoming", "calendar_search",
                  "calendar_create", "calendar_delete"]),
    ("Documents", ["read_document", "analyze_image", "analyze_images", "analyze_video",
                   "ocr_image", "analyze_document_smart"]),
    ("Faces", ["detect_faces", "recognize_face", "register_face", "list_registered_faces"]),
    ("Output", ["speak", "telegram_send"]),
]


def render_section_7() -> str:
    try:
        from agent.tools import get_tool_definitions
        tools = get_tool_definitions()
    except Exception:
        return "(tool inventory unavailable — agent.tools failed to import)"

    by_name = {t["name"]: t for t in tools}
    used: set = set()

    lines: List[str] = []
    for label, names in _TOOL_GROUPS:
        present = [n for n in names if n in by_name]
        if not present:
            continue
        used.update(present)
        lines.append(f"**{label}** ({len(present)}): " + " · ".join(present))

    leftover = sorted(set(by_name) - used)
    if leftover:
        lines.append(f"**Other** ({len(leftover)}): " + " · ".join(leftover))

    lines.append("")
    lines.append(f"**Total: {len(tools)} tools.**")
    return "\n".join(lines)


# ── Section §8: Open tickets ─────────────────────────────────────────────────

def render_section_8() -> str:
    if not TICKETS_OPEN.exists():
        TICKETS_OPEN.mkdir(parents=True, exist_ok=True)

    rows: List[Tuple[str, str, str, str]] = []
    for path in sorted(TICKETS_OPEN.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        rows.append((
            data.get("id", path.stem.split("-")[0]),
            (data.get("title", "(no title)") or "")[:80],
            data.get("severity", "P3"),
            (data.get("component", "") or "")[:40],
        ))

    if not rows:
        return "| ID | Title | Sev | Component |\n|---|---|---|---|\n| — | (none open) | — | — |"

    lines = ["| ID | Title | Sev | Component |", "|---|---|---|---|"]
    for tid, title, sev, comp in rows:
        # escape pipes in user-content cells
        title = title.replace("|", "\\|")
        comp = comp.replace("|", "\\|")
        lines.append(f"| {tid} | {title} | {sev} | {comp} |")
    return "\n".join(lines)


# ── Section §9: Recent solutions ─────────────────────────────────────────────

def _solution_title(sol: Dict) -> str:
    """Two schemas exist — newer ones have 'title', older ones have 'problem'."""
    if sol.get("title"):
        return sol["title"][:80]
    if sol.get("problem"):
        return sol["problem"][:80]
    return "(no description)"


def _solution_ticket(sol: Dict) -> str:
    if sol.get("ticket"):
        return sol["ticket"]
    tids = sol.get("ticket_ids") or []
    return ", ".join(tids[:2]) if tids else "—"


def render_section_9(limit: int = 10) -> str:
    if not SOLUTIONS.exists():
        return "| Solution | Ticket | Title |\n|---|---|---|\n| — | — | (none yet) |"

    try:
        with open(SOLUTIONS, "r", encoding="utf-8") as f:
            entries = []
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return "| Solution | Ticket | Title |\n|---|---|---|\n| — | — | (read error) |"

    recent = list(reversed(entries))[:limit]
    lines = ["| Solution | Ticket | Title |", "|---|---|---|"]
    for sol in recent:
        sid = sol.get("id", "—")
        ticket = _solution_ticket(sol).replace("|", "\\|")
        title = _solution_title(sol).replace("|", "\\|")
        lines.append(f"| {sid} | {ticket} | {title} |")
    return "\n".join(lines)


# ── Marker replacement ───────────────────────────────────────────────────────

def replace_section(content: str, section_num: int, new_body: str) -> str:
    """Replace text between `<!-- BEGIN AUTO §N -->` and `<!-- END AUTO §N -->`."""
    begin = f"<!-- BEGIN AUTO §{section_num} -->"
    end = f"<!-- END AUTO §{section_num} -->"

    pattern = re.compile(
        re.escape(begin) + r".*?" + re.escape(end),
        re.DOTALL,
    )
    replacement = f"{begin}\n{new_body}\n{end}"
    if not pattern.search(content):
        # Section markers missing — return unchanged, caller will warn
        return content
    return pattern.sub(replacement, content)


def regenerate(pi_md_text: str) -> str:
    out = pi_md_text
    out = replace_section(out, 4, render_section_4())
    out = replace_section(out, 7, render_section_7())
    out = replace_section(out, 8, render_section_8())
    out = replace_section(out, 9, render_section_9())
    return out


def main() -> int:
    if not PI_MD.exists():
        print(f"[refresh_pi] {PI_MD} not found — nothing to refresh", file=sys.stderr)
        return 1

    original = PI_MD.read_text(encoding="utf-8")
    updated = regenerate(original)

    if "--check" in sys.argv:
        if updated != original:
            print("[refresh_pi] PI.md is stale — run `python scripts/refresh_pi.py`", file=sys.stderr)
            return 1
        print("[refresh_pi] PI.md is up to date.")
        return 0

    if "--dry-run" in sys.argv:
        if updated == original:
            print("[refresh_pi] No changes.")
            return 0
        # Minimal diff: which sections changed
        changed = []
        for n in (4, 7, 8, 9):
            begin = f"<!-- BEGIN AUTO §{n} -->"
            end = f"<!-- END AUTO §{n} -->"
            old = re.search(re.escape(begin) + r"(.*?)" + re.escape(end), original, re.DOTALL)
            new = re.search(re.escape(begin) + r"(.*?)" + re.escape(end), updated, re.DOTALL)
            if old and new and old.group(1) != new.group(1):
                changed.append(f"§{n}")
        print(f"[refresh_pi] DRY RUN: would update sections: {', '.join(changed) or 'none'}")
        return 0

    if updated == original:
        print("[refresh_pi] PI.md already up to date.")
        return 0

    PI_MD.write_text(updated, encoding="utf-8")
    print("[refresh_pi] PI.md regenerated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
