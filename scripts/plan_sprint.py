#!/usr/bin/env python3
"""
scripts/plan_sprint.py — Weekly sprint planner (T-044).

Run on Monday (or whenever you want to set the week's plan). Picks 5–7 tickets
from the open queue, asks for a one-line sprint goal, writes both into PI.md §3
and snapshots them to vault/notes/sprints/YYYY-WW.md.

USAGE
-----
    python scripts/plan_sprint.py             # interactive prompts
    python scripts/plan_sprint.py --auto      # picks top tickets non-interactively
    python scripts/plan_sprint.py --goal "..." --tickets T-042 T-043

The script never deletes tickets — it only writes a new §3 in PI.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PI_MD = ROOT / "PI.md"
TICKETS_OPEN = ROOT / "tickets" / "open"
SPRINT_DIR = ROOT / "vault" / "notes" / "sprints"


# ── Helpers ──────────────────────────────────────────────────────────────────

def iso_week_label(d: Optional[date] = None) -> str:
    """Return 'YYYY-WW' for the given date (default today)."""
    d = d or date.today()
    yr, wk, _ = d.isocalendar()
    return f"{yr}-W{wk:02d}"


def week_range(d: Optional[date] = None) -> Tuple[date, date]:
    """Return (Monday, Sunday) of the week containing d."""
    d = d or date.today()
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def load_open_tickets() -> List[Dict]:
    """Return open tickets sorted by severity (P0 first), then created date."""
    if not TICKETS_OPEN.exists():
        return []
    items: List[Dict] = []
    for p in sorted(TICKETS_OPEN.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if data.get("status") == "escalated":
            continue
        items.append(data)
    sev = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
    items.sort(key=lambda t: (sev.get(t.get("severity", "P3"), 3), t.get("created", "")))
    return items


# ── Section §3 rendering ─────────────────────────────────────────────────────

def render_section_3(goal: str, tickets: List[Dict],
                     start: date, end: date) -> str:
    """Build the markdown for §3 (between the markers we'll add)."""
    lines = [
        f"**Week of:** {start.isoformat()} → {end.isoformat()}",
        f"**Sprint goal:** {goal.strip()}",
        "",
        "**Tasks, in priority order:**",
        "",
    ]
    for i, t in enumerate(tickets, 1):
        tid = t.get("id", "T-???")
        title = (t.get("title", "") or "").replace("\n", " ").strip()[:90]
        sev = t.get("severity", "P3")
        lines.append(f"{i}. **{tid}** ({sev}) — {title}")
    if not tickets:
        lines.append("(no tickets selected — pick some from `tickets/open/`)")

    lines.append("")
    lines.append("When closed: move into §6 of `CHECKPOINTS/current.md` and §9 of this file (auto).")
    return "\n".join(lines)


# ── PI.md editing ────────────────────────────────────────────────────────────

_SECTION_3_HEADER = "## §3 NOW — this week's sprint"
_SECTION_4_HEADER = "## §4 State (auto-generated)"


def replace_section_3(pi_md_text: str, new_body: str) -> str:
    """Replace the contents between '## §3 ...' and '## §4 ...' headers."""
    pattern = re.compile(
        re.escape(_SECTION_3_HEADER) + r".*?(?=" + re.escape(_SECTION_4_HEADER) + r")",
        re.DOTALL,
    )
    replacement = f"{_SECTION_3_HEADER}\n\n{new_body}\n\n---\n\n"
    return pattern.sub(replacement, pi_md_text, count=1)


# ── Vault snapshot ───────────────────────────────────────────────────────────

def write_vault_snapshot(goal: str, tickets: List[Dict],
                         start: date, end: date) -> Path:
    SPRINT_DIR.mkdir(parents=True, exist_ok=True)
    label = iso_week_label(start)
    out = SPRINT_DIR / f"{label}.md"
    body = [
        f"# Sprint {label} — {start.isoformat()} → {end.isoformat()}",
        "",
        f"**Goal:** {goal}",
        "",
        f"_Snapshot taken: {datetime.now(timezone.utc).isoformat()}_",
        "",
        "## Planned tickets",
        "",
    ]
    for t in tickets:
        body.append(
            f"- **{t.get('id', 'T-???')}** ({t.get('severity', 'P3')}) — "
            f"{(t.get('title', '') or '').strip()[:100]}"
        )
        comp = t.get("component", "") or ""
        if comp:
            body.append(f"  - Component: `{comp}`")
        what = (t.get("what_failed", "") or "").strip()
        if what:
            body.append(f"  - What failed: {what[:200]}")
    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    return out


# ── Interactive picker ───────────────────────────────────────────────────────

def interactive_pick(tickets: List[Dict], default_n: int = 6) -> List[Dict]:
    if not tickets:
        return []
    print(f"\n{len(tickets)} open ticket(s):\n")
    for i, t in enumerate(tickets, 1):
        print(f"  {i:2d}. [{t.get('severity','P3')}] {t.get('id','?')} — "
              f"{(t.get('title','') or '')[:80]}")

    print()
    raw = input(
        f"Pick tickets by number (comma-separated), or [enter] for top {default_n}, "
        f"or 'all': "
    ).strip()
    if not raw:
        return tickets[:default_n]
    if raw.lower() == "all":
        return tickets
    chosen: List[Dict] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit():
            i = int(tok) - 1
            if 0 <= i < len(tickets):
                chosen.append(tickets[i])
        else:
            for t in tickets:
                if t.get("id") == tok.upper():
                    chosen.append(t)
                    break
    return chosen


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Plan Pi's weekly sprint.")
    ap.add_argument("--goal", type=str, default=None,
                    help="Sprint goal (one line). If omitted, prompts.")
    ap.add_argument("--tickets", nargs="*", default=None,
                    help="Specific ticket IDs to include (e.g. T-042 T-043).")
    ap.add_argument("--auto", action="store_true",
                    help="Non-interactive: pick top 6 by severity automatically.")
    ap.add_argument("--n", type=int, default=6,
                    help="How many tickets to pick in --auto mode (default 6).")
    args = ap.parse_args()

    open_tix = load_open_tickets()
    start, end = week_range()

    # Goal
    if args.goal:
        goal = args.goal.strip()
    elif args.auto:
        goal = "(no goal set — edit §3 of PI.md to fill in)"
    else:
        goal = input("Sprint goal (one line): ").strip() or "(no goal set)"

    # Tickets
    if args.tickets:
        chosen = []
        for tid in args.tickets:
            for t in open_tix:
                if t.get("id") == tid.upper():
                    chosen.append(t)
                    break
    elif args.auto:
        chosen = open_tix[: args.n]
    else:
        chosen = interactive_pick(open_tix, default_n=args.n)

    new_body = render_section_3(goal, chosen, start, end)

    if not PI_MD.exists():
        print(f"[plan_sprint] {PI_MD} not found", file=sys.stderr)
        return 1

    pi_text = PI_MD.read_text(encoding="utf-8")
    if _SECTION_3_HEADER not in pi_text or _SECTION_4_HEADER not in pi_text:
        print("[plan_sprint] PI.md is missing §3 / §4 headers — refusing to overwrite",
              file=sys.stderr)
        return 1

    updated = replace_section_3(pi_text, new_body)
    PI_MD.write_text(updated, encoding="utf-8")
    print(f"[plan_sprint] PI.md §3 updated with {len(chosen)} ticket(s)")

    snap = write_vault_snapshot(goal, chosen, start, end)
    print(f"[plan_sprint] vault snapshot: {snap.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
