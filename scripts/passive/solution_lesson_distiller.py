"""
scripts/passive/solution_lesson_distiller.py — SKILL 10

Passive solution lesson distiller.  Reads solutions/SOLUTIONS.jsonl and
extracts recurring patterns, common causes, and gaps worth noting.
NEVER modifies SOLUTIONS.jsonl.

Checks:
  1. Recency     — WARN if no solution logged in >30 days
  2. Patterns    — surface top recurring root-cause tags
  3. Gaps        — WARN if >20% of solutions have no root_cause field
  4. Duplicates  — WARN if multiple solutions share same title prefix

Output: reports/solution_lesson_distiller.md

CLI:
  python scripts/passive/solution_lesson_distiller.py --check
  python scripts/passive/solution_lesson_distiller.py --strict
  python scripts/passive/solution_lesson_distiller.py --quiet
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    REPORTS as _DEFAULT_REPORTS,
    Status,
    read_jsonl,
    write_report,
    status_to_exit_code,
    worst,
)

REPORT_FILE      = "solution_lesson_distiller.md"
SOLUTIONS_FILE   = "solutions/SOLUTIONS.jsonl"
RECENCY_DAYS     = 30
GAP_THRESHOLD    = 0.20   # 20% missing root_cause = WARN
DUPLICATE_PREFIX = 40     # chars to compare for duplicate detection


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ── Individual checks ─────────────────────────────────────────────────────────

def check_recency(solutions: List[Dict]) -> Tuple[Status, List[str]]:
    """WARN if no solution has been logged in RECENCY_DAYS days."""
    if not solutions:
        return Status.WARN, [
            "[warn] `solutions/SOLUTIONS.jsonl` is empty — "
            "no solutions have been logged yet"
        ]

    dates = []
    for s in solutions:
        raw = s.get("solved_at") or s.get("created_at") or s.get("date") or ""
        dt = _parse_dt(str(raw))
        if dt:
            dates.append(dt)

    if not dates:
        return Status.WARN, [
            "[warn] No parseable dates found in solutions — "
            "add a `solved_at` ISO timestamp field"
        ]

    most_recent = max(dates)
    days_ago = (_now_utc() - most_recent).days

    if days_ago > RECENCY_DAYS:
        return Status.WARN, [
            f"[warn] Most recent solution is {days_ago} days old "
            f"(threshold: {RECENCY_DAYS} days) — is solution logging stale?"
        ]
    return Status.PASS, [
        f"[ok] Most recent solution logged {days_ago} day(s) ago"
    ]


def check_patterns(solutions: List[Dict]) -> Tuple[Status, List[str]]:
    """Surface top recurring root-cause tags (informational — always PASS)."""
    if not solutions:
        return Status.PASS, ["[ok] No solutions to pattern-mine"]

    tag_counter: Counter = Counter()
    for s in solutions:
        tags = s.get("root_cause") or s.get("tags") or s.get("category") or ""
        if isinstance(tags, list):
            for t in tags:
                tag_counter[str(t).strip().lower()] += 1
        elif isinstance(tags, str) and tags.strip():
            for t in re.split(r"[,;|]", tags):
                t = t.strip().lower()
                if t:
                    tag_counter[t] += 1

    if not tag_counter:
        return Status.PASS, ["[ok] No root_cause tags found — nothing to pattern-mine"]

    lines = [f"[ok] Top root-cause patterns across {len(solutions)} solutions:"]
    for tag, count in tag_counter.most_common(5):
        pct = int(100 * count / len(solutions))
        lines.append(f"  - `{tag}` — {count}× ({pct}%)")
    return Status.PASS, lines


def check_gaps(solutions: List[Dict]) -> Tuple[Status, List[str]]:
    """WARN if too many solutions lack a root_cause field."""
    if not solutions:
        return Status.PASS, ["[ok] No solutions to check for gaps"]

    missing = sum(
        1 for s in solutions
        if not (s.get("root_cause") or s.get("tags") or s.get("category"))
    )
    gap_rate = missing / len(solutions)

    if gap_rate > GAP_THRESHOLD:
        pct = int(gap_rate * 100)
        return Status.WARN, [
            f"[warn] {missing}/{len(solutions)} solutions ({pct}%) "
            f"have no root_cause field — add tags for better pattern mining"
        ]
    return Status.PASS, [
        f"[ok] {missing}/{len(solutions)} solutions missing root_cause "
        f"({int(gap_rate*100)}%) — within threshold"
    ]


def check_duplicates(solutions: List[Dict]) -> Tuple[Status, List[str]]:
    """WARN if multiple solutions share the same title prefix."""
    if len(solutions) < 2:
        return Status.PASS, ["[ok] Fewer than 2 solutions — no duplicates possible"]

    prefix_counts: Counter = Counter()
    for s in solutions:
        title = (s.get("title") or s.get("problem") or "").strip()
        prefix = title[:DUPLICATE_PREFIX].lower()
        if prefix:
            prefix_counts[prefix] += 1

    dupes = {p: n for p, n in prefix_counts.items() if n > 1}
    if not dupes:
        return Status.PASS, ["[ok] No duplicate solution titles detected"]

    lines = [
        f"[warn] {len(dupes)} potentially duplicate solution group(s) — "
        "consider merging:"
    ]
    for prefix, count in sorted(dupes.items(), key=lambda x: -x[1])[:5]:
        lines.append(f"  - `{prefix}…` appears {count}×")
    return Status.WARN, lines


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    sol_path = root / SOLUTIONS_FILE
    try:
        solutions = read_jsonl(sol_path)
    except Exception:
        solutions = []

    checks = [
        ("## 1. Recency",    lambda: check_recency(solutions)),
        ("## 2. Patterns",   lambda: check_patterns(solutions)),
        ("## 3. Gaps",       lambda: check_gaps(solutions)),
        ("## 4. Duplicates", lambda: check_duplicates(solutions)),
    ]

    section_texts: List[str] = []
    all_statuses:  List[Status] = []

    for heading, fn in checks:
        status, lines = fn()
        all_statuses.append(status)
        section_texts.append(f"{heading}  \n**Result:** {status.value}\n")
        for line in lines:
            section_texts.append(f"- {line}")
        section_texts.append("")

    overall = worst(all_statuses)
    if strict and overall == Status.WARN:
        overall = Status.FAIL

    verdict = (
        "Solution log is healthy and well-structured."
        if overall == Status.PASS
        else "**Solution log issues detected** — review above."
    )

    summary = (
        "## Summary\n\n"
        f"- Overall: **{overall.value}**\n"
        f"- {verdict}\n"
        f"- Solutions analysed: {len(solutions)}\n"
        + (f"- Mode: `--strict` (WARN -> FAIL)\n" if strict else "")
        + "\n"
    )

    body = summary + "\n".join(section_texts)

    # Deep Haiku analysis — extract cross-cutting patterns from recent solutions
    if len(solutions) >= 5:
        try:
            from agent.skill_triage import deep_analysis
            # Last 20 solutions, structured for the LLM
            recent = solutions[-20:]
            context_lines = []
            for s in recent:
                sid = s.get("id", "?")
                title = s.get("title", "")[:80]
                root_cause = s.get("root_cause", "") or "(no root_cause tag)"
                context_lines.append(f"- {sid}: {title} — root: {root_cause[:120]}")
            context = "\n".join(context_lines)

            deep_md = deep_analysis(
                skill_name="solution_lesson_distiller",
                context=context,
                question=(
                    "What cross-cutting patterns appear in these recent solutions? "
                    "Are there repeated failure classes that suggest a systemic gap "
                    "(e.g. missing schema validation, repeated dict-key bugs, etc.)? "
                    "Suggest 2-3 concrete preventative measures."
                ),
            )
            if deep_md:
                body += "\n\n" + deep_md
        except Exception:
            pass

    write_report(REPORT_FILE, body, overall)
    return overall


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args:
        print(__doc__)
        return 0
    strict = "--strict" in args
    quiet  = "--quiet" in args
    status = run_check(strict=strict)
    if not quiet:
        icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]",
                "BLOCKED": "[BLOCKED]"}.get(status.value, "[?]")
        print(f"[solution_lesson_distiller] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")
    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
