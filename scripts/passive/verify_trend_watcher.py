"""
scripts/passive/verify_trend_watcher.py — SKILL 9

Passive verify trend watcher.  Reads analysis/verify_history.jsonl (appended
by verify.py after every run) and detects regressions, stagnation, or
worsening flakiness trends.  NEVER re-runs tests.

Checks:
  1. Trend direction  — FAIL if pass-rate has fallen ≥10 pp vs prior run
  2. Stagnation       — WARN if no improvement in last N runs
  3. Failure churn    — WARN if same tests keep failing across runs

Output: reports/verify_trend_watcher.md

CLI:
  python scripts/passive/verify_trend_watcher.py --check
  python scripts/passive/verify_trend_watcher.py --strict
  python scripts/passive/verify_trend_watcher.py --quiet
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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

REPORT_FILE      = "verify_trend_watcher.md"
HISTORY_FILE     = "analysis/verify_history.jsonl"
MIN_RUNS         = 2    # need at least this many runs for trend analysis
REGRESSION_PP    = 10   # percentage-point drop triggers FAIL
STAGNATION_RUNS  = 5    # runs with no improvement = WARN
CHURN_THRESHOLD  = 3    # times a test must fail to count as churning


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _pass_rate(record: Dict) -> Optional[float]:
    """Extract pass rate (0–100) from a verify history record."""
    total = record.get("total") or record.get("collected") or 0
    passed = record.get("passed") or 0
    if total > 0:
        return 100.0 * passed / total
    # Fall back to pre-computed field
    rate = record.get("pass_rate") or record.get("pass_pct")
    if rate is not None:
        return float(rate)
    return None


def _failed_tests(record: Dict) -> Set[str]:
    """Extract the set of failing test IDs from a record."""
    raw = record.get("failed_tests") or record.get("failures") or []
    if isinstance(raw, list):
        return {str(t).strip() for t in raw if t}
    if isinstance(raw, str):
        return {line.strip() for line in raw.splitlines() if line.strip()}
    return set()


# ── Individual checks ─────────────────────────────────────────────────────────

def check_trend_direction(runs: List[Dict]) -> Tuple[Status, List[str]]:
    """FAIL if latest run shows regression >= REGRESSION_PP vs prior run."""
    if len(runs) < MIN_RUNS:
        return Status.PASS, [
            f"[ok] Not enough runs for trend analysis (need ≥{MIN_RUNS})"
        ]

    prev_rate = _pass_rate(runs[-2])
    curr_rate = _pass_rate(runs[-1])

    if prev_rate is None or curr_rate is None:
        return Status.WARN, [
            "[warn] Missing pass_rate field in recent verify records — "
            "run verify.py to rebuild history"
        ]

    drop = prev_rate - curr_rate
    if drop >= REGRESSION_PP:
        return Status.FAIL, [
            f"[fail] Pass rate dropped {drop:.1f} pp "
            f"({prev_rate:.1f}% → {curr_rate:.1f}%) — regression detected"
        ]
    if drop > 0:
        return Status.WARN, [
            f"[warn] Pass rate slipped {drop:.1f} pp "
            f"({prev_rate:.1f}% → {curr_rate:.1f}%)"
        ]
    return Status.PASS, [
        f"[ok] Pass rate stable or improving "
        f"({prev_rate:.1f}% → {curr_rate:.1f}%)"
    ]


def check_stagnation(runs: List[Dict]) -> Tuple[Status, List[str]]:
    """WARN if no pass-rate improvement in the last STAGNATION_RUNS runs."""
    if len(runs) < STAGNATION_RUNS:
        return Status.PASS, [
            f"[ok] Fewer than {STAGNATION_RUNS} runs — stagnation check skipped"
        ]

    window = runs[-STAGNATION_RUNS:]
    rates = [_pass_rate(r) for r in window]
    rates = [r for r in rates if r is not None]

    if len(rates) < 2:
        return Status.PASS, ["[ok] Insufficient rate data for stagnation check"]

    best_early = max(rates[:-1])
    latest     = rates[-1]

    if latest <= best_early:
        return Status.WARN, [
            f"[warn] No improvement in last {STAGNATION_RUNS} runs "
            f"(best {best_early:.1f}%, latest {latest:.1f}%)"
        ]
    return Status.PASS, [
        f"[ok] Pass rate improved over last {STAGNATION_RUNS} runs "
        f"(was {best_early:.1f}%, now {latest:.1f}%)"
    ]


def check_failure_churn(runs: List[Dict]) -> Tuple[Status, List[str]]:
    """WARN if the same tests keep failing across multiple runs."""
    if len(runs) < MIN_RUNS:
        return Status.PASS, ["[ok] Not enough runs for churn analysis"]

    fail_count: Dict[str, int] = {}
    for r in runs:
        for t in _failed_tests(r):
            fail_count[t] = fail_count.get(t, 0) + 1

    churning = {t: n for t, n in fail_count.items() if n >= CHURN_THRESHOLD}
    if not churning:
        return Status.PASS, [
            f"[ok] No tests failing in ≥{CHURN_THRESHOLD} consecutive runs"
        ]

    lines = [
        f"[warn] {len(churning)} test(s) failing persistently "
        f"(≥{CHURN_THRESHOLD}× across {len(runs)} runs):"
    ]
    for t, n in sorted(churning.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"  - `{t}` failed {n}× ")
    return Status.WARN, lines


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    history_path = root / HISTORY_FILE
    try:
        runs = read_jsonl(history_path)
    except Exception:
        runs = []

    if not runs:
        write_report(
            REPORT_FILE,
            "## Summary\n\n"
            "- Overall: **PASS**\n"
            "- No verify history found — run `python verify.py` to start tracking.\n",
            Status.PASS,
        )
        return Status.PASS

    checks = [
        ("## 1. Trend Direction",  lambda: check_trend_direction(runs)),
        ("## 2. Stagnation",       lambda: check_stagnation(runs)),
        ("## 3. Failure Churn",    lambda: check_failure_churn(runs)),
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
        "Test suite is trending healthy."
        if overall == Status.PASS
        else "**Trend issues detected** — review above."
    )

    summary = (
        "## Summary\n\n"
        f"- Overall: **{overall.value}**\n"
        f"- {verdict}\n"
        f"- Runs analysed: {len(runs)}\n"
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
        print(f"[verify_trend_watcher] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")
    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
