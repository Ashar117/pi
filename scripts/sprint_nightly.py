#!/usr/bin/env python3
"""
scripts/sprint_nightly.py — T-202: Nightly sprint wrapper for unattended runs.

Checks for sprint.disabled flag before running, enforces a failure budget
(3 consecutive escalations → auto-disable + loud alert), and sends a
Telegram morning summary of what happened.

USAGE
-----
    python scripts/sprint_nightly.py              # actual run (--auto-implement)
    python scripts/sprint_nightly.py --dry-run    # plan only, no commits
    python scripts/sprint_nightly.py --disable    # write sprint.disabled + exit

KILL SWITCH
-----------
Create file <repo_root>/sprint.disabled to stop all nightly runs.
Delete it to re-enable. The script exits 0 (not an error) when disabled.

FAILURE BUDGET
--------------
Consecutive sprint escalations/failures are tracked in logs/sprint/nightly.jsonl.
When consecutive_failures >= 3:
  - sprint.disabled is written automatically
  - A loud Telegram alert is sent
  - Future runs exit early until Ash reviews and removes sprint.disabled
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DISABLED_FLAG = ROOT / "sprint.disabled"
NIGHTLY_LOG = ROOT / "logs" / "sprint" / "nightly.jsonl"
MAX_CONSECUTIVE_FAILURES = 3

# Conservative nightly defaults: one ticket, tight cost cap, safe-only components.
NIGHTLY_FLAGS = ["--auto-implement", "--max-tickets", "1", "--max-cost", "0.50"]


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _telegram_send(msg: str) -> None:
    try:
        from tools.tools_telegram import send_message  # type: ignore
        send_message(msg)
    except Exception:
        pass


# ── Failure budget tracking ───────────────────────────────────────────────────

def _read_nightly_log() -> list:
    if not NIGHTLY_LOG.exists():
        return []
    entries = []
    for line in NIGHTLY_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _count_consecutive_failures(entries: list) -> int:
    """Count consecutive failure/escalation entries from the most recent run."""
    count = 0
    for entry in reversed(entries):
        if entry.get("outcome") in ("failure", "escalated"):
            count += 1
        else:
            break
    return count


def _append_nightly_log(entry: dict) -> None:
    NIGHTLY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with NIGHTLY_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Nightly sprint wrapper (T-202).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Plan only — no commits, no file edits.")
    ap.add_argument("--disable", action="store_true",
                    help="Write sprint.disabled and exit (manual kill switch).")
    ap.add_argument("--max-tickets", type=int, default=1)
    ap.add_argument("--max-cost", type=float, default=0.50)
    args = ap.parse_args()

    now = datetime.now(timezone.utc).isoformat()

    # Manual kill switch
    if args.disable:
        DISABLED_FLAG.write_text("disabled by --disable flag\n", encoding="utf-8")
        _telegram_send("[Pi sprint] Nightly sprint manually disabled via --disable.")
        print("[sprint_nightly] disabled.")
        return 0

    # Check kill-switch flag
    if DISABLED_FLAG.exists():
        reason = DISABLED_FLAG.read_text(encoding="utf-8", errors="replace").strip()
        print(f"[sprint_nightly] Sprint disabled: {reason}")
        return 0

    # Check failure budget
    log_entries = _read_nightly_log()
    consecutive = _count_consecutive_failures(log_entries)
    if consecutive >= MAX_CONSECUTIVE_FAILURES:
        DISABLED_FLAG.write_text(
            f"auto-disabled: {consecutive} consecutive failures\n", encoding="utf-8"
        )
        _telegram_send(
            f"[Pi sprint] DISABLED after {consecutive} consecutive failures. "
            "Review logs/sprint/nightly.jsonl and delete sprint.disabled to re-enable."
        )
        print(f"[sprint_nightly] Failure budget exceeded ({consecutive}). Sprint disabled.")
        return 1

    # Build sprint.py command
    sprint_script = ROOT / "scripts" / "sprint.py"
    cmd = [sys.executable, str(sprint_script)] + NIGHTLY_FLAGS + [
        "--max-tickets", str(args.max_tickets),
        "--max-cost", str(args.max_cost),
    ]
    if args.dry_run:
        cmd = [sys.executable, str(sprint_script), "--dry-run",
               "--max-tickets", str(args.max_tickets)]

    print(f"[sprint_nightly] Running: {' '.join(cmd)}", flush=True)
    start = datetime.now(timezone.utc)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=900,  # 15-min hard wall per run
            encoding="utf-8",
            errors="replace",
        )
        rc = result.returncode
        stdout = result.stdout[-2000:] if result.stdout else ""
        stderr = result.stderr[-500:] if result.stderr else ""
    except subprocess.TimeoutExpired:
        rc = -1
        stdout = "[timeout after 900s]"
        stderr = ""
    except Exception as exc:
        rc = -2
        stdout = f"[subprocess error: {exc}]"
        stderr = ""

    duration_s = (datetime.now(timezone.utc) - start).total_seconds()

    # Parse outcome from sprint output
    outcome = "success"
    if rc != 0:
        outcome = "failure"
    elif "escalated" in stdout.lower():
        outcome = "escalated"
    elif "nothing safe" in stdout.lower() or "no tickets" in stdout.lower():
        outcome = "empty_queue"

    # Log the run
    log_entry = {
        "ts": now,
        "outcome": outcome,
        "exit_code": rc,
        "duration_s": round(duration_s, 1),
        "dry_run": args.dry_run,
        "tail": stdout[-500:],
    }
    _append_nightly_log(log_entry)

    # Telegram morning summary
    emoji = {"success": "[OK]", "escalated": "[!]", "failure": "[ERR]", "empty_queue": "[--]"}
    tag = emoji.get(outcome, "[?]")
    summary_lines = [f"[Pi sprint] Nightly run {now[:10]}: {tag} {outcome.upper()}"]
    if outcome == "empty_queue":
        summary_lines.append("Nothing safe in queue — no work done.")
    elif outcome == "escalated":
        summary_lines.append("Ticket required human review — escalated.")
    elif outcome == "failure":
        summary_lines.append(f"Exit code {rc}. Tail: {stdout[-200:]}")
    else:
        summary_lines.append(f"Completed in {duration_s:.0f}s.")

    new_consec = _count_consecutive_failures(_read_nightly_log())
    if new_consec >= MAX_CONSECUTIVE_FAILURES - 1:
        summary_lines.append(
            f"Warning: {new_consec} consecutive non-successes — "
            f"{MAX_CONSECUTIVE_FAILURES - new_consec} more until auto-disable."
        )

    _telegram_send("\n".join(summary_lines))
    print(f"[sprint_nightly] Done: {outcome} (exit {rc}, {duration_s:.0f}s)")
    return 0 if outcome in ("success", "empty_queue") else 1


if __name__ == "__main__":
    sys.exit(main())
