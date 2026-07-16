"""
scripts/passive/turns_log_rotate.py — T-259

Gzip-archives logs/turns.jsonl once it exceeds ~50MB, then truncates the
live file so appends resume immediately. Archives land in logs/archive/
as turns_jsonl-<ts>.jsonl.gz — the exact pattern agent/turn_log.py's
recent_turns() already walks, so archived history stays queryable.

CLI:
  python scripts/passive/turns_log_rotate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import Status, write_report, status_to_exit_code


def run_rotate(quiet: bool = False):
    from agent.turn_log import rotate_turns_log, log_path

    status = Status.PASS
    if not log_path().exists():
        line = "No logs/turns.jsonl present — nothing to rotate."
        if not quiet:
            print("[turns_log_rotate] nothing to rotate")
        return status, line

    try:
        archived = rotate_turns_log()
    except Exception as e:
        if not quiet:
            print(f"[turns_log_rotate] FAIL: {e}")
        return Status.FAIL, f"ERROR rotating logs/turns.jsonl: {e}"

    if archived:
        line = f"Rotated logs/turns.jsonl -> {archived.name}"
        if not quiet:
            print(f"[turns_log_rotate] {status.value} — {line}")
    else:
        line = "logs/turns.jsonl below rotation threshold — nothing to do."
        if not quiet:
            print("[turns_log_rotate] below threshold, nothing to do")

    return status, line


def main():
    quiet = "--quiet" in sys.argv
    status, content = run_rotate(quiet=quiet)
    write_report("turns_log_rotate.md", content, status)
    sys.exit(status_to_exit_code(status))


if __name__ == "__main__":
    main()
