"""
scripts/passive/session_exit_protocol_checker.py — SKILL 2

Passive session-exit protocol checker.  Verifies the end-of-session
checklist was followed before closing a chat.  NEVER auto-fixes anything.

Checks:
  1. verify.py run recently     — STATUS.md mtime within last 30 minutes  → WARN
  2. Verify status is PASS      — parse docs/STATUS.md                    → FAIL
  3. PI.md refreshed today      — PI.md mtime is today (UTC)              → WARN
  4. CHECKPOINTS updated today  — CHECKPOINTS/current.md mtime is today   → WARN
  5. No FAIL reports in reports/— scan reports/*.md for Status: FAIL      → FAIL
  6. Privacy guard PASS         — reports/privacy_publish_guard.md status → FAIL
  7. Git working tree clean     — git status --short empty                → WARN

CLI:
  python scripts/passive/session_exit_protocol_checker.py --check
  python scripts/passive/session_exit_protocol_checker.py --strict
  python scripts/passive/session_exit_protocol_checker.py --quiet
  python scripts/passive/session_exit_protocol_checker.py --help
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    REPORTS as _DEFAULT_REPORTS,
    STATUS_MD,
    CHECKPOINTS,
    Status,
    git_status_short,
    write_report,
    status_to_exit_code,
    worst,
)

REPORT_FILE = "session_exit_protocol.md"

# How recently verify.py must have run (seconds)
VERIFY_RECENCY_SECS = 30 * 60  # 30 minutes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _file_mtime_utc(path: Path) -> Optional[datetime]:
    """Return file mtime as UTC datetime, or None if file missing."""
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _is_today_utc(path: Path) -> bool:
    """Return True if file mtime is today (UTC date)."""
    mtime = _file_mtime_utc(path)
    if mtime is None:
        return False
    return mtime.date() == datetime.now(timezone.utc).date()


def _seconds_since(path: Path) -> Optional[float]:
    """Return seconds since file was last modified, or None if missing."""
    mtime = _file_mtime_utc(path)
    if mtime is None:
        return None
    return (datetime.now(timezone.utc) - mtime).total_seconds()


def _parse_overall_status(status_md_text: str) -> Optional[str]:
    """Extract Overall: PASS/FAIL from STATUS.md content."""
    m = re.search(r"\*\*Overall:\*\*\s*(\w+)", status_md_text)
    return m.group(1).upper() if m else None


def _report_status(report_path: Path) -> Optional[str]:
    """Read **Status:** VALUE from a skill report file."""
    if not report_path.exists():
        return None
    m = re.search(r"\*\*Status:\*\*\s*(\w+)", report_path.read_text(encoding="utf-8", errors="replace"))
    return m.group(1).upper() if m else None


# ── Individual checks ─────────────────────────────────────────────────────────

def check_verify_recency(status_md: Path) -> Tuple[Status, List[str]]:
    """WARN if STATUS.md is older than VERIFY_RECENCY_SECS."""
    age = _seconds_since(status_md)
    if age is None:
        return Status.WARN, [
            f"⚠ `docs/STATUS.md` not found — has verify.py ever run?  "
            f"*(run: `python scripts/verify.py`)*"
        ]
    minutes = int(age // 60)
    if age <= VERIFY_RECENCY_SECS:
        return Status.PASS, [f"[ok] verify.py ran {minutes}m ago"]
    return Status.WARN, [
        f"⚠ verify.py last ran {minutes}m ago (threshold: {VERIFY_RECENCY_SECS // 60}m)  "
        f"*(run: `python scripts/verify.py`)*"
    ]


def check_verify_pass(status_md: Path) -> Tuple[Status, List[str]]:
    """FAIL if docs/STATUS.md shows FAIL overall."""
    if not status_md.exists():
        return Status.WARN, ["⚠ `docs/STATUS.md` missing — cannot confirm verify PASS"]
    text = status_md.read_text(encoding="utf-8", errors="replace")
    overall = _parse_overall_status(text)
    if overall is None:
        return Status.WARN, ["⚠ Could not parse Overall status from `docs/STATUS.md`"]
    if overall == "PASS":
        return Status.PASS, ["[ok] `docs/STATUS.md` Overall: PASS"]
    return Status.FAIL, [
        f"[FAIL] `docs/STATUS.md` Overall: {overall}  "
        f"*(fix failures then re-run `python scripts/verify.py`)*"
    ]


def check_pi_md_refreshed(pi_md: Path) -> Tuple[Status, List[str]]:
    """WARN if PI.md has not been modified today (UTC)."""
    if not pi_md.exists():
        return Status.WARN, ["⚠ `PI.md` not found"]
    if _is_today_utc(pi_md):
        return Status.PASS, ["[ok] `PI.md` modified today"]
    mtime = _file_mtime_utc(pi_md)
    date_str = mtime.strftime("%Y-%m-%d") if mtime else "unknown"
    return Status.WARN, [
        f"⚠ `PI.md` last modified {date_str} (not today)  "
        f"*(run: `python scripts/refresh_pi.py`)*"
    ]


def check_checkpoints_updated(checkpoints_md: Path) -> Tuple[Status, List[str]]:
    """WARN if CHECKPOINTS/current.md has not been modified today."""
    if not checkpoints_md.exists():
        return Status.WARN, ["⚠ `CHECKPOINTS/current.md` not found"]
    if _is_today_utc(checkpoints_md):
        return Status.PASS, ["[ok] `CHECKPOINTS/current.md` updated today"]
    mtime = _file_mtime_utc(checkpoints_md)
    date_str = mtime.strftime("%Y-%m-%d") if mtime else "unknown"
    return Status.WARN, [
        f"⚠ `CHECKPOINTS/current.md` last modified {date_str} (not today)  "
        f"*(update before closing session)*"
    ]


def check_no_fail_reports(reports_dir: Path) -> Tuple[Status, List[str]]:
    """FAIL if any report in reports/ has Status: FAIL."""
    if not reports_dir.exists():
        return Status.PASS, ["[ok] No reports/ directory yet — nothing to check"]

    fail_reports: List[str] = []
    for md in sorted(reports_dir.glob("*.md")):
        status_val = _report_status(md)
        if status_val == "FAIL":
            fail_reports.append(
                f"`reports/{md.name}` — Status: FAIL  "
                f"*(investigate and resolve before pushing)*"
            )

    if not fail_reports:
        return Status.PASS, ["[ok] No FAIL status in any skill report"]
    return Status.FAIL, fail_reports


def check_privacy_guard_pass(reports_dir: Path) -> Tuple[Status, List[str]]:
    """FAIL if privacy_publish_guard.md is missing or not PASS."""
    report = reports_dir / "privacy_publish_guard.md"
    if not report.exists():
        return Status.WARN, [
            "⚠ `reports/privacy_publish_guard.md` not found  "
            "*(run: `python scripts/passive/privacy_publish_guard.py --check`)*"
        ]
    status_val = _report_status(report)
    if status_val == "PASS":
        return Status.PASS, ["[ok] Privacy guard: PASS"]
    if status_val == "WARN":
        return Status.WARN, [
            f"⚠ Privacy guard: WARN — review `reports/privacy_publish_guard.md`"
        ]
    return Status.FAIL, [
        f"[FAIL] Privacy guard: {status_val or 'unknown'}  "
        f"*(run: `python scripts/passive/privacy_publish_guard.py --check`)*"
    ]


def check_git_clean() -> Tuple[Status, List[str]]:
    """WARN if git working tree has uncommitted changes."""
    dirty = git_status_short()
    if not dirty:
        return Status.PASS, ["[ok] Git working tree is clean"]
    lines_count = len(dirty.splitlines())
    return Status.WARN, [
        f"⚠ Git working tree has {lines_count} uncommitted change(s)  "
        f"*(commit or stash before closing)*"
    ]


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    """Run all session-exit checks; write report; return overall Status."""
    status_md   = root / "docs" / "STATUS.md"
    pi_md       = root / "PI.md"
    checkpoints = root / "CHECKPOINTS" / "current.md"

    checks = [
        ("## 1. Verify Ran Recently",
         lambda: check_verify_recency(status_md)),
        ("## 2. Verify Status PASS",
         lambda: check_verify_pass(status_md)),
        ("## 3. PI.md Refreshed Today",
         lambda: check_pi_md_refreshed(pi_md)),
        ("## 4. CHECKPOINTS Updated Today",
         lambda: check_checkpoints_updated(checkpoints)),
        ("## 5. No FAIL Reports",
         lambda: check_no_fail_reports(reports)),
        ("## 6. Privacy Guard PASS",
         lambda: check_privacy_guard_pass(reports)),
        ("## 7. Git Working Tree Clean",
         check_git_clean),
    ]

    section_texts: List[str] = []
    all_statuses: List[Status] = []

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

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = (
        "## Summary\n\n"
        f"- Checked: {now_str}\n"
        f"- Overall: **{overall.value}**\n"
        + (f"- Mode: `--strict` (WARN → FAIL)\n" if strict else "")
        + "\n"
    )

    write_report(REPORT_FILE, summary + "\n".join(section_texts), overall)
    return overall


# ── CLI ───────────────────────────────────────────────────────────────────────

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
        print(f"[session_exit_protocol_checker] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")

    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
