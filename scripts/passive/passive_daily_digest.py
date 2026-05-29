"""
scripts/passive/passive_daily_digest.py — SKILL 13

Passive daily digest.  Runs all 13 passive observer skills, collects
their statuses, and emits a single consolidated report + summary line.
This is the "heartbeat" skill — run it once a day to see Pi's health at
a glance.  NEVER auto-fixes anything; it only observes and reports.

Output: reports/passive_daily_digest.md

CLI:
  python scripts/passive/passive_daily_digest.py --check
  python scripts/passive/passive_daily_digest.py --strict
  python scripts/passive/passive_daily_digest.py --quiet
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    REPORTS as _DEFAULT_REPORTS,
    Status,
    write_report,
    status_to_exit_code,
    worst,
)

REPORT_FILE = "passive_daily_digest.md"

# All 13 skill modules in order
SKILL_MODULES = [
    ("privacy_publish_guard",           "SKILL 1  · Privacy Guard"),
    ("session_exit_protocol_checker",   "SKILL 2  · Session Continuity"),
    ("doc_drift_watcher",               "SKILL 3  · Doc Drift"),
    ("sprint_readiness_checker",        "SKILL 4  · Sprint Readiness"),
    ("consciousness_capability_sync",   "SKILL 5  · Capability Sync"),
    ("half_baked_feature_detector",     "SKILL 6  · Half-Baked Features"),
    ("autonomy_loop_watcher",           "SKILL 7  · Autonomy Loop"),
    ("ticket_candidate_miner",          "SKILL 8  · Ticket Candidates"),
    ("verify_trend_watcher",            "SKILL 9  · Verify Trends"),
    ("solution_lesson_distiller",       "SKILL 10 · Solution Lessons"),
    ("tech_debt_accumulator",           "SKILL 11 · Tech Debt"),
    ("memory_pollution_detector",       "SKILL 12 · Memory Pollution"),
    ("silent_failure_watcher",          "SKILL 13 · Silent Failures"),
    ("conversation_ticket_miner",       "SKILL 14 · Conversation→Ticket"),
]

STATUS_ICON = {
    "PASS":    "✓",
    "WARN":    "!",
    "FAIL":    "✗",
    "BLOCKED": "?",
}


def _run_skill(module_name: str, strict: bool, root: Path, reports: Path) -> Tuple[str, Status]:
    """Import and run a single skill's run_check. Return (module_name, status)."""
    try:
        import importlib
        mod = importlib.import_module(f"scripts.passive.{module_name}")
        import inspect
        sig = inspect.signature(mod.run_check)
        kwargs = {"strict": strict}
        if "root" in sig.parameters:
            kwargs["root"] = root
        if "reports" in sig.parameters:
            kwargs["reports"] = reports
        status = mod.run_check(**kwargs)
        return module_name, status
    except Exception as e:
        return module_name, Status.BLOCKED


def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
    parallel: bool = True,
    max_workers: int = 4,
) -> Status:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Parallel execution: each skill is I/O bound (file reads, optional LLM call)
    # so a small thread pool gives a wall-clock speedup without overwhelming
    # the Groq endpoint with concurrent triage requests.
    results: List[Tuple[str, str, Status]] = []
    if parallel:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="passive-skill") as pool:
            futures = {
                pool.submit(_run_skill, module_name, strict=strict, root=root, reports=reports): (module_name, label)
                for module_name, label in SKILL_MODULES
            }
            interim: dict = {}
            for fut in as_completed(futures):
                module_name, label = futures[fut]
                try:
                    _, status = fut.result()
                except Exception:
                    status = Status.BLOCKED
                interim[module_name] = (label, status)
        # Preserve SKILL_MODULES order in the scorecard
        for module_name, label in SKILL_MODULES:
            label2, status = interim.get(module_name, (label, Status.BLOCKED))
            results.append((module_name, label2, status))
    else:
        for module_name, label in SKILL_MODULES:
            _, status = _run_skill(module_name, strict=strict, root=root, reports=reports)
            results.append((module_name, label, status))

    all_statuses = [s for _, _, s in results]
    overall = worst(all_statuses)
    if strict and overall == Status.WARN:
        overall = Status.FAIL

    # Count by status
    counts = {s.value: 0 for s in Status}
    for _, _, s in results:
        counts[s.value] = counts.get(s.value, 0) + 1

    # Build scorecard
    scorecard_lines: List[str] = []
    for module_name, label, status in results:
        icon = STATUS_ICON.get(status.value, "?")
        scorecard_lines.append(f"| {icon} {status.value:<8} | {label} |")

    summary = (
        f"## Pi Passive Daily Digest — {now_str}\n\n"
        "## Summary\n\n"
        f"- Overall: **{overall.value}**\n"
        f"- PASS: {counts.get('PASS',0)}  ·  WARN: {counts.get('WARN',0)}  ·  "
        f"FAIL: {counts.get('FAIL',0)}  ·  BLOCKED: {counts.get('BLOCKED',0)}\n"
        + (f"- Mode: `--strict` (WARN -> FAIL)\n" if strict else "")
        + "\n## Skill Scorecard\n\n"
        "| Status   | Skill |\n"
        "|----------|-------|\n"
    )

    full_report = summary + "\n".join(scorecard_lines) + "\n\n---\n\n"
    full_report += "## Individual Reports\n\n"
    for module_name, label, status in results:
        report_path = reports / f"{module_name}.md"
        if report_path.exists():
            try:
                text = report_path.read_text(encoding="utf-8", errors="replace")
                # Truncate to first 30 lines to keep digest manageable
                excerpt = "\n".join(text.splitlines()[:30])
                full_report += f"### {label}\n\n{excerpt}\n\n"
            except OSError:
                full_report += f"### {label}\n\n*Report not readable.*\n\n"
        else:
            full_report += f"### {label}\n\n*No report yet — run skill individually.*\n\n"

    write_report(REPORT_FILE, full_report, overall)
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
        print(f"[passive_daily_digest] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")
    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
