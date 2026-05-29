"""
scripts/passive/autonomy_loop_watcher.py — SKILL 7

Passive autonomy-loop watcher.  Monitors Pi's autonomous scripts and
detects silent failures.  NEVER auto-fixes anything.

Checks:
  1. sprint.py activity     — WARN if no sprint logs in 14 days; WARN if escalation >50%
  2. plan_sprint.py cadence — WARN if PI.md §3 not updated this week
  3. retro.py cadence       — WARN if last week's retro note is missing
  4. refresh_pi.py drift    — WARN if PI.md §4 counts differ from actual

CLI:
  python scripts/passive/autonomy_loop_watcher.py --check
  python scripts/passive/autonomy_loop_watcher.py --strict
  python scripts/passive/autonomy_loop_watcher.py --quiet
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

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

REPORT_FILE = "autonomy_loop_watcher.md"

SPRINT_IDLE_DAYS   = 14
ESCALATION_WARN    = 0.50   # 50%


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_safe(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_to_dt(s: str) -> Optional[datetime]:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _current_iso_week(now: Optional[datetime] = None) -> str:
    """Return ISO week string YYYY-Www for the given (or current) datetime."""
    dt = now or _now_utc()
    return dt.strftime("%G-W%V")


def _last_iso_week(now: Optional[datetime] = None) -> str:
    dt = now or _now_utc()
    last = dt - timedelta(weeks=1)
    return last.strftime("%G-W%V")


# ── Individual checks ─────────────────────────────────────────────────────────

def check_sprint_activity(
    root: Path,
    idle_days: int = SPRINT_IDLE_DAYS,
    escalation_threshold: float = ESCALATION_WARN,
) -> Tuple[Status, List[str]]:
    """WARN if sprint has been idle >idle_days or escalation rate is too high."""
    logs_dir = root / "logs" / "sprint"
    if not logs_dir.exists():
        return Status.WARN, [
            "[warn] `logs/sprint/` not found — sprint.py may never have run  "
            "*(or logs are in a different location)*"
        ]

    # Find most recent log file
    log_files = sorted(logs_dir.glob("*.jsonl")) + sorted(logs_dir.glob("*.json"))
    if not log_files:
        return Status.WARN, [
            "[warn] No sprint log files found in `logs/sprint/`"
        ]

    # Check recency of most recent log
    most_recent = log_files[-1]
    mtime = datetime.fromtimestamp(most_recent.stat().st_mtime, tz=timezone.utc)
    days_ago = (_now_utc() - mtime).days

    lines: List[str] = []
    statuses: List[Status] = []

    if days_ago > idle_days:
        lines.append(
            f"[warn] Last sprint log is {days_ago} days old  "
            f"*(sprint.py hasn't run in >{idle_days} days)*"
        )
        statuses.append(Status.WARN)
    else:
        lines.append(f"[ok] Sprint ran {days_ago} day(s) ago")
        statuses.append(Status.PASS)

    # Compute escalation rate from all sprint logs
    closed = escalated = 0
    for lf in log_files:
        try:
            records = read_jsonl(lf)
        except Exception:
            continue
        for r in records:
            outcome = (r.get("outcome") or r.get("status") or "").lower()
            if outcome in ("closed", "done", "success"):
                closed += 1
            elif outcome in ("escalated", "failed", "blocked"):
                escalated += 1

    total = closed + escalated
    if total > 0:
        rate = escalated / total
        pct  = int(rate * 100)
        if rate > escalation_threshold:
            lines.append(
                f"[warn] Escalation rate {pct}% ({escalated}/{total}) exceeds "
                f"{int(escalation_threshold*100)}% threshold"
            )
            statuses.append(Status.WARN)
        else:
            lines.append(f"[ok] Escalation rate {pct}% ({escalated}/{total})")
            statuses.append(Status.PASS)

    return worst(statuses) if statuses else Status.PASS, lines


def check_plan_sprint_cadence(root: Path, now: Optional[datetime] = None) -> Tuple[Status, List[str]]:
    """WARN if PI.md §3 week-of date is not the current ISO week."""
    pi_md = root / "PI.md"
    text  = _read_safe(pi_md)
    if text is None:
        return Status.WARN, ["[warn] `PI.md` not found — cannot check sprint plan cadence"]

    # Look for "Week of: YYYY-MM-DD" in §3
    m = re.search(r"\*\*Week of:\*\*\s*(\d{4}-\d{2}-\d{2})", text)
    if m is None:
        return Status.WARN, ["[warn] Could not find '**Week of:**' in PI.md §3"]

    week_start = _iso_to_dt(m.group(1))
    if week_start is None:
        return Status.WARN, ["[warn] Could not parse week-of date in PI.md §3"]

    current_week = _current_iso_week(now)
    pi_week      = _current_iso_week(week_start)

    if pi_week != current_week:
        return Status.WARN, [
            f"[warn] PI.md §3 week is `{pi_week}`, current week is `{current_week}`  "
            f"*(run `python scripts/plan_sprint.py` to update)*"
        ]

    # Also check vault/notes/sprints/YYYY-Www.md exists
    sprint_note = root / "vault" / "notes" / "sprints" / f"{current_week}.md"
    if not sprint_note.exists():
        return Status.WARN, [
            f"[warn] Sprint note `vault/notes/sprints/{current_week}.md` not found  "
            f"*(run `python scripts/plan_sprint.py`)*"
        ]

    return Status.PASS, [f"[ok] Sprint plan current (week {current_week})"]


def check_retro_cadence(root: Path, now: Optional[datetime] = None) -> Tuple[Status, List[str]]:
    """WARN if last week's retro note is missing."""
    last_week  = _last_iso_week(now)
    retro_note = root / "vault" / "notes" / "retros" / f"{last_week}.md"

    if not retro_note.exists():
        return Status.WARN, [
            f"[warn] Retro note `vault/notes/retros/{last_week}.md` not found  "
            f"*(run `python scripts/retro.py` for last week's retro)*"
        ]
    return Status.PASS, [f"[ok] Retro note exists for {last_week}"]


def check_refresh_pi_drift(root: Path) -> Tuple[Status, List[str]]:
    """WARN if PI.md §4 counts don't match actual repo state (delegates to doc_drift logic)."""
    try:
        from scripts.passive.doc_drift_watcher import (
            check_open_tickets,
            check_closed_tickets,
            check_solution_count,
            check_verify_status,
        )
        results = [
            check_open_tickets(root),
            check_closed_tickets(root),
            check_solution_count(root),
            check_verify_status(root),
        ]
        drifts = [(s, ls) for s, ls in results if s != Status.PASS]
        if not drifts:
            return Status.PASS, ["[ok] PI.md §4 is in sync — refresh_pi.py is current"]
        lines = ["[warn] PI.md §4 is stale — run `python scripts/refresh_pi.py`:"]
        for _, ls in drifts:
            lines.extend(f"  {l}" for l in ls if l.strip())
        return Status.WARN, lines
    except ImportError:
        return Status.WARN, ["[warn] Could not import doc_drift_watcher — Skill 4 not built"]


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    checks = [
        ("## 1. sprint.py Activity",
         lambda: check_sprint_activity(root)),
        ("## 2. plan_sprint.py Cadence",
         lambda: check_plan_sprint_cadence(root)),
        ("## 3. retro.py Cadence",
         lambda: check_retro_cadence(root)),
        ("## 4. refresh_pi.py Drift",
         lambda: check_refresh_pi_drift(root)),
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
        "All autonomy loops are healthy."
        if overall == Status.PASS
        else "**Autonomy loop issues detected** — review above."
        if overall in (Status.WARN, Status.FAIL)
        else "Could not fully check — see above."
    )

    summary = (
        "## Summary\n\n"
        f"- Overall: **{overall.value}**\n"
        f"- {verdict}\n"
        + (f"- Mode: `--strict` (WARN -> FAIL)\n" if strict else "")
        + "\n"
    )

    write_report(REPORT_FILE, summary + "\n".join(section_texts), overall)
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
        print(f"[autonomy_loop_watcher] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")
    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
