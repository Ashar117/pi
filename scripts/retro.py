#!/usr/bin/env python3
"""
scripts/retro.py — Weekly retrospective generator (T-045).

Reads the past 7 days of activity and writes a markdown retro to
vault/notes/retros/YYYY-WW.md. Optionally pings Telegram.

Sources used:
  - tickets/closed/*.json  → tickets shipped this week
  - solutions/SOLUTIONS.jsonl → solutions filed
  - logs/turns.jsonl → conversation volume + cost (turn_log)
  - logs/evolution.jsonl → tool-call costs, error rates
  - git log (best-effort) → commits this week

USAGE
-----
    python scripts/retro.py                # writes retro for current ISO week
    python scripts/retro.py --week 2026-W18
    python scripts/retro.py --notify       # send retro summary via Telegram
    python scripts/retro.py --stdout       # print to stdout instead of writing
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TICKETS_CLOSED = ROOT / "tickets" / "closed"
SOLUTIONS = ROOT / "solutions" / "SOLUTIONS.jsonl"
TURNS_LOG = ROOT / "logs" / "turns.jsonl"
EVOLUTION_LOG = ROOT / "logs" / "evolution.jsonl"
RETROS_DIR = ROOT / "vault" / "notes" / "retros"


# ── Week math ────────────────────────────────────────────────────────────────

def parse_week(label: Optional[str]) -> Tuple[date, date, str]:
    """Return (monday, sunday, 'YYYY-Www') for the given ISO week label or today."""
    if label:
        m = re.match(r"^(\d{4})-W(\d{2})$", label)
        if not m:
            raise ValueError(f"bad week label: {label!r} (expected 'YYYY-Www')")
        yr, wk = int(m.group(1)), int(m.group(2))
        monday = date.fromisocalendar(yr, wk, 1)
    else:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    yr, wk, _ = monday.isocalendar()
    return monday, sunday, f"{yr}-W{wk:02d}"


# ── Data collectors ──────────────────────────────────────────────────────────

def closed_tickets_in_range(start: date, end: date) -> List[Dict]:
    if not TICKETS_CLOSED.exists():
        return []
    out: List[Dict] = []
    for p in sorted(TICKETS_CLOSED.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        ts = data.get("closed") or data.get("created") or ""
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except Exception:
            continue
        if start <= d <= end:
            out.append(data)
    out.sort(key=lambda t: t.get("closed", ""))
    return out


def solutions_in_range(start: date, end: date) -> List[Dict]:
    if not SOLUTIONS.exists():
        return []
    out: List[Dict] = []
    for line in SOLUTIONS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            sol = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = sol.get("date", "")
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except Exception:
            continue
        if start <= d <= end:
            out.append(sol)
    return out


def turns_in_range(start: date, end: date) -> List[Dict]:
    if not TURNS_LOG.exists():
        return []
    out: List[Dict] = []
    for line in TURNS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = entry.get("ts", "")
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except Exception:
            continue
        if start <= d <= end:
            out.append(entry)
    return out


def evolution_in_range(start: date, end: date) -> List[Dict]:
    if not EVOLUTION_LOG.exists():
        return []
    out: List[Dict] = []
    for line in EVOLUTION_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = rec.get("timestamp", "")
        try:
            d = datetime.fromisoformat(ts).date()
        except Exception:
            continue
        if start <= d <= end:
            out.append(rec)
    return out


def commits_in_range(start: date, end: date) -> List[str]:
    """Best-effort git log. Returns list of 'sha title' strings."""
    try:
        r = subprocess.run(
            ["git", "log", f"--since={start.isoformat()}",
             f"--until={(end + timedelta(days=1)).isoformat()}",
             "--pretty=format:%h %s"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return []
        return [l for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


# ── Aggregation ──────────────────────────────────────────────────────────────

def summarise(closed: List[Dict], solutions: List[Dict], turns: List[Dict],
              evolution: List[Dict], commits: List[str]) -> Dict:
    total_cost_turns = sum(float(t.get("cost", 0) or 0) for t in turns)
    total_cost_evo = sum(float(r.get("cost", 0) or 0) for r in evolution)
    total_cost = round(max(total_cost_turns, total_cost_evo), 4)

    by_mode: Dict[str, int] = {}
    for t in turns:
        m = t.get("mode", "unknown")
        by_mode[m] = by_mode.get(m, 0) + 1

    failed_evo = [r for r in evolution if r.get("success") is False]
    error_rate = (len(failed_evo) / len(evolution)) if evolution else 0.0

    tool_counts: Dict[str, int] = {}
    for r in evolution:
        for name in r.get("tools_used", []) or []:
            tool_counts[name] = tool_counts.get(name, 0) + 1
        for tc in r.get("tool_calls", []) or []:
            n = tc.get("name", "")
            if n:
                tool_counts[n] = tool_counts.get(n, 0) + 1
    top_tools = sorted(tool_counts.items(), key=lambda x: -x[1])[:8]

    return {
        "tickets_closed": len(closed),
        "solutions_filed": len(solutions),
        "turns": len(turns),
        "by_mode": by_mode,
        "total_cost_usd": total_cost,
        "error_rate": round(error_rate, 3),
        "top_tools": top_tools,
        "commits": len(commits),
    }


# ── Markdown render ──────────────────────────────────────────────────────────

def render_retro(label: str, start: date, end: date,
                 closed: List[Dict], solutions: List[Dict],
                 commits: List[str], summary: Dict) -> str:
    lines: List[str] = [
        f"# Retro {label} — {start.isoformat()} → {end.isoformat()}",
        "",
        f"_Generated {datetime.now(timezone.utc).isoformat()}_",
        "",
        "## At a glance",
        "",
        f"- **Tickets shipped:** {summary['tickets_closed']}",
        f"- **Solutions filed:** {summary['solutions_filed']}",
        f"- **Conversation turns:** {summary['turns']} "
        f"(by mode: " + ", ".join(f"{m}:{n}" for m, n in summary['by_mode'].items()) + ")",
        f"- **Spend (USD):** ${summary['total_cost_usd']:.4f}",
        f"- **Tool error rate:** {summary['error_rate']*100:.1f}%",
        f"- **Commits:** {summary['commits']}",
        "",
    ]

    if summary["top_tools"]:
        lines.append("## Top tools")
        lines.append("")
        for name, n in summary["top_tools"]:
            lines.append(f"- `{name}` × {n}")
        lines.append("")

    lines.append("## Tickets shipped")
    lines.append("")
    if closed:
        for t in closed:
            lines.append(
                f"- **{t.get('id', '?')}** ({t.get('severity', 'P3')}) — "
                f"{(t.get('title', '') or '').strip()[:90]}"
            )
            sol = t.get("linked_solution")
            if sol:
                lines.append(f"  - Solution: `{sol}`")
    else:
        lines.append("- (none — light week or in-flight)")
    lines.append("")

    if commits:
        lines.append("## Commits")
        lines.append("")
        for c in commits[:30]:
            lines.append(f"- `{c}`")
        if len(commits) > 30:
            lines.append(f"- … +{len(commits) - 30} more")
        lines.append("")

    lines.append("## What to do differently next week")
    lines.append("")
    lines.append("_(Hand-fill or have Pi propose during planning.)_")
    lines.append("")

    return "\n".join(lines) + "\n"


# ── Telegram notify ──────────────────────────────────────────────────────────

def telegram_notify(label: str, summary: Dict, retro_path: Path) -> None:
    try:
        from tools.tools_telegram import send_message
    except Exception:
        return
    msg = (
        f"*Pi retro {label}*\n"
        f"Tickets shipped: {summary['tickets_closed']}\n"
        f"Solutions: {summary['solutions_filed']}\n"
        f"Turns: {summary['turns']}\n"
        f"Spend: ${summary['total_cost_usd']:.4f}\n"
        f"Error rate: {summary['error_rate']*100:.1f}%\n"
        f"File: `{retro_path.name}`"
    )
    try:
        send_message(msg)
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────────────────────

def build_self_model(solutions_path: Path = SOLUTIONS, max_lines: int = 30) -> str:
    """T-193: Distill SOLUTIONS.jsonl into a compact self-model block (~30 lines).

    Counts recurring root-cause patterns from solution summaries, identifies
    top bug classes and confirmed strengths. No LLM required — pure counting.
    Returns a markdown block suitable for L3 injection (category='self_model').
    """
    from collections import Counter

    # Load solutions
    sols: List[Dict] = []
    if solutions_path.exists():
        for line in solutions_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    sols.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not sols:
        return "## Pi Self-Model\n\n*(No solutions recorded yet.)*"

    # Extract root-cause keywords from summaries
    root_cause_keywords = [
        ("write_read_divergence", ["write.*read", "diverge", "read.*path.*write.*path", "tier.*bug"]),
        ("missing_tests",         ["no test", "without test", "untested", "test coverage"]),
        ("stale_docs",            ["drift", "doc.*stale", "outdated", "hand.edit", "auto.*section"]),
        ("honesty_gap",           ["hallucin", "mime", "fake", "pretend", "incorrect.*claim"]),
        ("context_drop",          ["context.*drop", "conversation.*coherence", "T-148", "prior turn"]),
        ("tool_registration",     ["tool.*spec", "registry", "not registered", "tool.*missing"]),
    ]
    counts: Counter = Counter()
    for sol in sols:
        text = (sol.get("summary") or sol.get("title") or "").lower()
        for label, patterns in root_cause_keywords:
            if any(re.search(p, text) for p in patterns):
                counts[label] += 1

    # Build the block
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: List[str] = [
        f"## Pi Self-Model (distilled {now_str} from {len(sols)} solutions)",
        "",
        "### Recurring Bug Classes (ranked)",
    ]
    if counts:
        for label, cnt in counts.most_common(6):
            lines.append(f"- **{label.replace('_', ' ')}** — {cnt} solution(s)")
    else:
        lines.append("- no clear pattern yet")

    lines += [
        "",
        "### Engineering loop stats",
        f"- Total solutions recorded: {len(sols)}",
        f"- Most recent: {sols[-1].get('id', '?')} — {sols[-1].get('title', '')[:60]}",
        "",
        "### Standing commitments",
        "- verify.py MUST pass before any ticket close",
        "- Never mime tool calls — claim effects only after tool_result seen",
        "- Read before write — use read_file before modify_file",
        "- 3-seg prompt cache: only DYNAMIC segment changes per turn",
    ]

    # Cap at max_lines
    return "\n".join(lines[:max_lines])


def write_self_model_to_l3(model_text: str, memory_tools=None) -> Dict:
    """T-193: Write the self-model as a pinned L3 entry, replacing any prior version.

    Searches for existing 'self_model' category entry and deletes before writing
    so the block refreshes in-place rather than accumulating duplicates.
    """
    if memory_tools is None:
        return {"success": False, "error": "no memory_tools provided"}
    try:
        # Delete any existing self_model entry
        existing = memory_tools.memory_read(query="self_model", tier="l3", limit=5)
        for entry in existing:
            if entry.get("category") == "self_model":
                eid = entry.get("id")
                if eid:
                    try:
                        memory_tools.memory_delete(eid)
                    except Exception:
                        pass
        # Write fresh entry
        result = memory_tools.memory_write(
            content=model_text,
            tier="l3",
            category="self_model",
            importance=9,
        )
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Pi's weekly retrospective.")
    ap.add_argument("--week", type=str, default=None,
                    help="ISO week label like '2026-W18'. Default: this week.")
    ap.add_argument("--notify", action="store_true",
                    help="Send a summary to Telegram after writing.")
    ap.add_argument("--stdout", action="store_true",
                    help="Print to stdout instead of writing to vault.")
    ap.add_argument("--self-model", action="store_true",
                    help="Build + write the identity self-model to L3 (T-193).")
    args = ap.parse_args()

    if args.self_model:
        model_text = build_self_model()
        print(model_text)
        return 0

    try:
        start, end, label = parse_week(args.week)
    except ValueError as e:
        print(f"[retro] {e}", file=sys.stderr)
        return 1

    closed = closed_tickets_in_range(start, end)
    sols = solutions_in_range(start, end)
    turns = turns_in_range(start, end)
    evo = evolution_in_range(start, end)
    commits = commits_in_range(start, end)

    summary = summarise(closed, sols, turns, evo, commits)
    body = render_retro(label, start, end, closed, sols, commits, summary)

    if args.stdout:
        print(body)
        return 0

    RETROS_DIR.mkdir(parents=True, exist_ok=True)
    out = RETROS_DIR / f"{label}.md"
    out.write_text(body, encoding="utf-8")
    print(f"[retro] wrote {out.relative_to(ROOT)}")

    if args.notify:
        telegram_notify(label, summary, out)
        print("[retro] telegram notification sent (if configured)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
