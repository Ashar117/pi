"""
scripts/passive/silent_failure_watcher.py — SKILL 14

Passive silent-failure watcher. Reads from data/silent_failures.db
(populated by agent.observability.track_silent) and reports trends.
NEVER modifies any data.

Checks:
  1. Top-5 categories by 24h event count
  2. WARN if any single category exceeds PI_SILENT_FAILURE_WARN_PER_CAT (default 50)
  3. FAIL if total 24h events exceed PI_SILENT_FAILURE_FAIL_TOTAL (default 500)

Output: reports/silent_failure_watcher.md

CLI:
  python scripts/passive/silent_failure_watcher.py --check
  python scripts/passive/silent_failure_watcher.py --strict
  python scripts/passive/silent_failure_watcher.py --quiet
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

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

REPORT_FILE = "silent_failure_watcher.md"
_DB_PATH = _DEFAULT_ROOT / "data" / "silent_failures.db"

# T-265: runtime error alerting — categories worth waking Ash up for, even
# outside the 24h WARN/FAIL trend view above. Matched by exact name or suffix
# since router-derived categories are "<router_name>.all_exhausted".
_P1_EXACT = {"agent.process_input"}
_P1_SUFFIXES = (".all_exhausted", ".session_exit_error")
_ALERT_STATE_PATH = _DEFAULT_ROOT / "data" / "silent_failure_alerts_sent.json"


def _is_p1_category(category: str) -> bool:
    return category in _P1_EXACT or category.endswith(_P1_SUFFIXES)


def _load_alert_state(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_alert_state(path: Path, state: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def send_p1_alerts(
    counts: Dict[str, int],
    state_path: Path = _ALERT_STATE_PATH,
) -> List[str]:
    """T-265: send one throttled Telegram alert per P1 category per day.

    Returns the list of categories actually alerted on this call (for
    reporting/testing) — empty if nothing new, Telegram unavailable, or
    no P1 categories present. Never raises.
    """
    p1_hits = {cat: cnt for cat, cnt in counts.items() if _is_p1_category(cat) and cnt > 0}
    if not p1_hits:
        return []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = _load_alert_state(state_path)
    alerted: List[str] = []

    try:
        from tools.tools_telegram import send_message
    except ImportError:
        return []

    for cat, cnt in p1_hits.items():
        if state.get(cat) == today:
            continue  # already alerted this category today
        result = send_message(f"[Pi runtime alert] {cat} failed {cnt}x in the last 24h.")
        if result.get("success"):
            state[cat] = today
            alerted.append(cat)

    if alerted:
        _save_alert_state(state_path, state)
    return alerted

# Env-configurable thresholds
_WARN_PER_CAT_DEFAULT = 50
_FAIL_TOTAL_DEFAULT = 500


def _get_thresholds() -> Tuple[int, int]:
    def _int_env(key: str, default: int) -> int:
        try:
            return int(os.environ.get(key, ""))
        except (ValueError, TypeError):
            return default

    warn_per_cat = _int_env("PI_SILENT_FAILURE_WARN_PER_CAT", _WARN_PER_CAT_DEFAULT)
    fail_total = _int_env("PI_SILENT_FAILURE_FAIL_TOTAL", _FAIL_TOTAL_DEFAULT)
    return warn_per_cat, fail_total


def _read_24h_counts(db_path: Path) -> Dict[str, int]:
    """Return {category: count} for the last 24 hours."""
    if not db_path.exists():
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT category, COUNT(*) FROM silent_failures "
            "WHERE timestamp > ? GROUP BY category ORDER BY COUNT(*) DESC",
            (cutoff,),
        ).fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}


def _read_top_exception_types(db_path: Path, category: str, limit: int = 5) -> List[str]:
    """Return recent unique exception_type values for a given category."""
    if not db_path.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT DISTINCT exception_type FROM silent_failures "
            "WHERE category = ? AND timestamp > ? AND exception_type IS NOT NULL "
            "LIMIT ?",
            (category, cutoff, limit),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def check_silent_failures(
    db_path: Path,
    warn_per_cat: int,
    fail_total: int,
) -> Tuple[Status, List[str]]:
    counts = _read_24h_counts(db_path)
    total = sum(counts.values())
    lines: List[str] = []

    if not counts:
        lines.append("No silent failures recorded in the last 24h.")
        return Status.PASS, lines

    top5 = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:5]
    lines.append(f"Total silent failures (24h): **{total}**")
    lines.append("")
    lines.append("**Top 5 categories:**")
    for cat, cnt in top5:
        flag = " [WARN]" if cnt >= warn_per_cat else ""
        lines.append(f"- `{cat}`: {cnt}{flag}")

    # Section 2: exception types in top category
    if top5:
        top_cat = top5[0][0]
        exc_types = _read_top_exception_types(db_path, top_cat)
        if exc_types:
            lines.append("")
            lines.append(f"**Recent exception types in `{top_cat}`:**")
            for et in exc_types:
                lines.append(f"- `{et}`")

    if total >= fail_total:
        lines.append(f"\nFAIL: total events {total} >= threshold {fail_total}")
        return Status.FAIL, lines

    cat_warn = any(cnt >= warn_per_cat for _, cnt in top5)
    if cat_warn:
        lines.append(f"\nWARN: one or more categories exceed per-category threshold ({warn_per_cat})")
        return Status.WARN, lines

    lines.append(f"\nAll counts within thresholds (warn_per_cat={warn_per_cat}, fail_total={fail_total}).")
    return Status.PASS, lines


def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    db_path = root / "data" / "silent_failures.db"
    warn_per_cat, fail_total = _get_thresholds()

    status, lines = check_silent_failures(db_path, warn_per_cat, fail_total)
    if strict and status == Status.WARN:
        status = Status.FAIL

    # T-265: throttled Telegram alert for P1-class categories, independent
    # of the WARN/FAIL trend thresholds above — a single provider-exhausted
    # event matters even if the 24h total never crosses the trend threshold.
    alerted = send_p1_alerts(_read_24h_counts(db_path), state_path=root / "data" / "silent_failure_alerts_sent.json")
    if alerted:
        lines.append(f"\nAlerted via Telegram: {', '.join(alerted)}")

    verdict = (
        "No silent failure trends detected."
        if status == Status.PASS
        else "**Silent failure trends detected** — review categories above."
    )

    summary = (
        "## Summary\n\n"
        f"- Overall: **{status.value}**\n"
        f"- {verdict}\n"
        + (f"- Mode: `--strict` (WARN -> FAIL)\n" if strict else "")
        + f"- Thresholds: warn_per_cat={warn_per_cat}, fail_total={fail_total}\n"
        + "\n"
    )

    section = "## 1. Silent Failures (24h)  \n"
    section += f"**Result:** {status.value}\n\n"
    section += "\n".join(lines) + "\n"

    body = summary + section

    # LLM triage — identify root cause patterns across categories
    if status != Status.PASS and lines:
        try:
            from agent.skill_triage import triage
            triage_md = triage(
                skill_name="silent_failure_watcher",
                findings_summary=f"Status {status.value} — silent failures recorded over the last 24h",
                raw_lines=lines,
                question="Identify root-cause patterns: are these failures all from one subsystem, or scattered? Which category most warrants a ticket?",
            )
            if triage_md:
                body += "\n\n" + triage_md
        except Exception:
            pass

    write_report(REPORT_FILE, body, status)
    return status


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pi passive skill 14: silent failure watcher")
    parser.add_argument("--check", action="store_true", help="Run the check")
    parser.add_argument("--strict", action="store_true", help="Escalate WARN to FAIL")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args = parser.parse_args()

    status = run_check(strict=args.strict)
    if not args.quiet:
        print(f"[Silent Failure Watcher] {status.value}")
    sys.exit(status_to_exit_code(status))
