"""
scripts/passive/daemon_staleness_checker.py — SKILL 14

Passive daemon-staleness checker. Reads data/daemon_info.json (written by
pi_daemon.py at startup, T-284) and compares it to the repo's current git
state. A live daemon can silently run code the repo has moved past — this
happened for weeks: l3_cache truncation (T-270), real email sends (T-271),
and unauthorized button taps (T-278) all stayed live in production because
nothing compared "code running" to "code in the repo." NEVER restarts the
daemon — this only reports.

Checks:
  1. daemon_info.json exists — WARN if not (daemon never ran with T-284, or isn't running)
  2. daemon's git_rev matches current HEAD — WARN if not
  3. any tracked .py file's mtime is newer than the daemon's started_at — WARN
  4. drift age > 7 days — FAIL

Output: reports/daemon_staleness_checker.md

CLI:
  python scripts/passive/daemon_staleness_checker.py --check
  python scripts/passive/daemon_staleness_checker.py --strict
  python scripts/passive/daemon_staleness_checker.py --quiet
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    Status,
    run_git,
    write_report,
    status_to_exit_code,
)

REPORT_FILE = "daemon_staleness_checker.md"
_STALE_FAIL_DAYS = 7


def check_staleness(root: Path = _DEFAULT_ROOT) -> tuple[Status, List[str]]:
    """Compare data/daemon_info.json to the repo's current git state."""
    info_path = root / "data" / "daemon_info.json"
    if not info_path.exists():
        return Status.WARN, ["daemon_info.json missing — daemon not running, or predates T-284"]

    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return Status.WARN, [f"daemon_info.json unreadable: {e}"]

    lines: List[str] = []
    status = Status.PASS

    started_at_raw = info.get("started_at")
    started_at = None
    if started_at_raw:
        try:
            started_at = datetime.fromisoformat(started_at_raw)
        except ValueError:
            lines.append(f"started_at unparseable: {started_at_raw!r}")

    current_rev = run_git(["rev-parse", "HEAD"]).stdout.strip()
    daemon_rev = info.get("git_rev")
    if daemon_rev and current_rev and daemon_rev != current_rev:
        lines.append(
            f"daemon is running {daemon_rev[:12]} but repo HEAD is {current_rev[:12]} — restart the daemon"
        )
        status = Status.WARN

    if started_at is not None:
        newer_files = []
        for py_file in sorted(root.rglob("*.py")):
            if any(part in py_file.parts for part in
                   ("pi_env", "__pycache__", ".git", ".claude", "_archive", "testing")):
                continue
            try:
                mtime = datetime.fromtimestamp(py_file.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime > started_at:
                newer_files.append(str(py_file.relative_to(root)))
        if newer_files:
            lines.append(
                f"{len(newer_files)} file(s) modified after daemon startup — restart the daemon: "
                + ", ".join(newer_files[:5])
                + (" ..." if len(newer_files) > 5 else "")
            )
            status = Status.WARN

        age_days = (datetime.now(timezone.utc) - started_at).total_seconds() / 86400
        if age_days > _STALE_FAIL_DAYS and status == Status.WARN:
            lines.append(f"drift has persisted {age_days:.1f} days (> {_STALE_FAIL_DAYS}) — restart the daemon now")
            status = Status.FAIL

    if not lines:
        lines = ["daemon code matches repo HEAD, no newer files detected"]

    return status, lines


def run_check(strict: bool = False, root: Path = _DEFAULT_ROOT) -> Status:
    status, lines = check_staleness(root)
    if strict and status == Status.WARN:
        status = Status.FAIL

    icon = {"PASS": "[ok]", "WARN": "[warn]", "FAIL": "[fail]"}.get(status.value, "[?]")
    body = (
        "## Summary\n\n"
        f"- Overall: **{status.value}**\n\n"
        "## Daemon vs Repo\n\n"
        + "\n".join(f"- {icon} {l}" for l in lines)
    )
    write_report(REPORT_FILE, body, status)
    return status


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args:
        print(__doc__)
        return 0
    strict = "--strict" in args
    quiet = "--quiet" in args
    status = run_check(strict=strict)
    if not quiet:
        icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}.get(status.value, "[?]")
        print(f"[daemon_staleness_checker] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")
    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
