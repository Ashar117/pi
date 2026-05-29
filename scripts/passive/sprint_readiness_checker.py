"""
scripts/passive/sprint_readiness_checker.py — SKILL 3

Passive sprint-readiness checker.  Verifies the repo is safe for
scripts/sprint.py to run autonomously.  NEVER auto-fixes anything.

Checks:
  1. Git working tree clean     — FAIL  (sprint needs a clean tree to branch)
  2. Verify status PASS         — FAIL  (broken tests must be fixed first)
  3. Privacy guard PASS         — FAIL  (no leaks before automation commits)
  4. Doc drift not FAIL         — WARN  (stale docs won't block sprint)
  5. No open P0 / P1 tickets    — FAIL  (urgent issues need manual attention)
  6. Not on main / master       — WARN  (sprint should work on a feature branch)
  7. .env present and non-empty — FAIL  (sprint.py needs API keys)

CLI:
  python scripts/passive/sprint_readiness_checker.py --check
  python scripts/passive/sprint_readiness_checker.py --strict
  python scripts/passive/sprint_readiness_checker.py --quiet
  python scripts/passive/sprint_readiness_checker.py --help
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    REPORTS as _DEFAULT_REPORTS,
    TICKETS_OPEN,
    Status,
    git_status_short,
    run_git,
    write_report,
    status_to_exit_code,
    worst,
)

REPORT_FILE = "sprint_readiness.md"

# Ticket severities that block sprint automation
BLOCKING_SEVERITIES = {"P0", "P1"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _report_status(report_path: Path) -> Optional[str]:
    """Read **Status:** VALUE from a skill report file."""
    if not report_path.exists():
        return None
    import re
    m = re.search(
        r"\*\*Status:\*\*\s*(\w+)",
        report_path.read_text(encoding="utf-8", errors="replace"),
    )
    return m.group(1).upper() if m else None


def _overall_from_status_md(status_md: Path) -> Optional[str]:
    """Extract Overall: PASS/FAIL from STATUS.md."""
    if not status_md.exists():
        return None
    import re
    m = re.search(
        r"\*\*Overall:\*\*\s*(\w+)",
        status_md.read_text(encoding="utf-8", errors="replace"),
    )
    return m.group(1).upper() if m else None


def get_current_branch() -> str:
    """Return current git branch name, or 'unknown' on failure."""
    r = run_git(["branch", "--show-current"])
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    # Fallback for detached HEAD / older git
    r2 = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if r2.returncode == 0 and r2.stdout.strip():
        return r2.stdout.strip()
    return "unknown"


# ── Individual checks ─────────────────────────────────────────────────────────

def check_git_clean() -> Tuple[Status, List[str]]:
    """FAIL if working tree has uncommitted changes."""
    dirty = git_status_short()
    if not dirty:
        return Status.PASS, ["[ok] Git working tree is clean"]
    n = len(dirty.splitlines())
    return Status.FAIL, [
        f"[FAIL] {n} uncommitted change(s) in working tree  "
        f"*(commit or stash before running sprint.py)*"
    ]


def check_verify_pass(status_md: Path) -> Tuple[Status, List[str]]:
    """FAIL if docs/STATUS.md is missing or not PASS."""
    overall = _overall_from_status_md(status_md)
    if overall is None:
        return Status.FAIL, [
            "⚠ `docs/STATUS.md` missing or unparseable  "
            "*(run: `python scripts/verify.py`)*"
        ]
    if overall == "PASS":
        return Status.PASS, ["[ok] Verify status: PASS"]
    return Status.FAIL, [
        f"[FAIL] Verify status: {overall}  "
        f"*(fix test failures then re-run `python scripts/verify.py`)*"
    ]


def check_privacy_guard(reports_dir: Path) -> Tuple[Status, List[str]]:
    """FAIL if privacy_publish_guard.md is missing or FAIL; WARN if WARN."""
    report = reports_dir / "privacy_publish_guard.md"
    val = _report_status(report)
    if val is None:
        return Status.FAIL, [
            "⚠ `reports/privacy_publish_guard.md` not found  "
            "*(run: `python scripts/passive/privacy_publish_guard.py --check`)*"
        ]
    if val == "PASS":
        return Status.PASS, ["[ok] Privacy guard: PASS"]
    if val == "WARN":
        return Status.WARN, [
            "⚠ Privacy guard: WARN — review before sprint commits anything  "
            "*(see `reports/privacy_publish_guard.md`)*"
        ]
    return Status.FAIL, [
        f"[FAIL] Privacy guard: {val}  "
        f"*(resolve privacy issues before running sprint.py)*"
    ]


def check_doc_drift(reports_dir: Path) -> Tuple[Status, List[str]]:
    """WARN if doc_drift_watcher report is FAIL (Skill 4); skip if not yet built."""
    report = reports_dir / "doc_drift_watcher.md"
    if not report.exists():
        return Status.PASS, [
            "[skip] `reports/doc_drift_watcher.md` not found — Skill 4 not yet built"
        ]
    val = _report_status(report)
    if val == "FAIL":
        return Status.WARN, [
            "⚠ Doc drift detected (doc_drift_watcher: FAIL)  "
            "*(run `python scripts/refresh_pi.py` to sync docs)*"
        ]
    return Status.PASS, [f"[ok] Doc drift watcher: {val or 'ok'}"]


def check_no_blocking_tickets(tickets_open: Path) -> Tuple[Status, List[str]]:
    """FAIL if any open P0 or P1 ticket exists."""
    if not tickets_open.exists():
        return Status.PASS, ["[ok] No open tickets directory — nothing blocking"]

    blocking: List[str] = []
    for path in sorted(tickets_open.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        sev = (data.get("severity") or data.get("sev") or "").upper()
        if sev in BLOCKING_SEVERITIES:
            tid = data.get("id", path.stem)
            title = (data.get("title") or "(no title)")[:60]
            blocking.append(
                f"`{tid}` [{sev}] {title}  "
                f"*(resolve manually before running sprint.py)*"
            )

    if not blocking:
        return Status.PASS, ["[ok] No open P0/P1 tickets"]
    return Status.FAIL, blocking


def check_branch(branch: Optional[str] = None) -> Tuple[Status, List[str]]:
    """WARN if on main or master branch."""
    current = branch if branch is not None else get_current_branch()
    if current in ("main", "master"):
        return Status.WARN, [
            f"⚠ Currently on `{current}` branch  "
            f"*(sprint.py should run on a feature branch, not `{current}`)*"
        ]
    if current == "unknown":
        return Status.WARN, ["⚠ Could not detect current branch"]
    return Status.PASS, [f"[ok] On branch `{current}` (not main/master)"]


def check_env_file(root: Path) -> Tuple[Status, List[str]]:
    """FAIL if .env is missing or empty."""
    env_path = root / ".env"
    if not env_path.exists():
        return Status.FAIL, [
            "⚠ `.env` not found  "
            "*(sprint.py requires API keys — create `.env` from `.env.example`)*"
        ]
    # Count non-blank, non-comment lines without exposing values
    lines = [
        l for l in env_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    if not lines:
        return Status.FAIL, [
            "⚠ `.env` exists but is empty  "
            "*(add required API keys before running sprint.py)*"
        ]
    return Status.PASS, [f"[ok] `.env` present ({len(lines)} key(s) configured)"]


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    """Run all sprint-readiness checks; write report; return overall Status."""
    status_md   = root / "docs" / "STATUS.md"
    tickets_dir = root / "tickets" / "open"

    checks = [
        ("## 1. Git Working Tree Clean",
         check_git_clean),
        ("## 2. Verify Status PASS",
         lambda: check_verify_pass(status_md)),
        ("## 3. Privacy Guard PASS",
         lambda: check_privacy_guard(reports)),
        ("## 4. Doc Drift Not FAIL",
         lambda: check_doc_drift(reports)),
        ("## 5. No Open P0 / P1 Tickets",
         lambda: check_no_blocking_tickets(tickets_dir)),
        ("## 6. Not on main / master",
         check_branch),
        ("## 7. .env Present and Non-Empty",
         lambda: check_env_file(root)),
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

    verdict = (
        "Safe to run `python scripts/sprint.py`."
        if overall == Status.PASS
        else "**NOT safe to run sprint.py** — resolve issues above first."
        if overall == Status.FAIL
        else "Sprint can run, but review warnings above."
    )

    summary = (
        "## Summary\n\n"
        f"- Overall: **{overall.value}**\n"
        f"- {verdict}\n"
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
        print(f"[sprint_readiness_checker] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")

    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
