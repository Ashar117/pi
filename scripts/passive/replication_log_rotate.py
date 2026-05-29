"""
scripts/passive/replication_log_rotate.py — T-087 R6 pre-work

Daily rotation of data/memory_replication.log. Rotates the active log to
data/memory_replication.YYYY-MM-DD.log and starts a fresh file. Keeps logs
bounded — deletes rotated files older than 30 days.

CLI:
  python scripts/passive/replication_log_rotate.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import ROOT, Status, write_report, status_to_exit_code

KEEP_DAYS = 30


def run_rotate(root: Path = ROOT, quiet: bool = False):
    data_dir = root / "data"
    active   = data_dir / "memory_replication.log"
    lines    = []
    status   = Status.PASS

    if not active.exists():
        lines.append("No memory_replication.log present — nothing to rotate.")
        if not quiet:
            print("[replication_log_rotate] nothing to rotate")
        return status, "\n".join(lines)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rotated = data_dir / f"memory_replication.{today}.log"

    try:
        active.rename(rotated)
        size = rotated.stat().st_size
        lines.append(f"Rotated memory_replication.log → {rotated.name} ({size} bytes)")
    except Exception as e:
        lines.append(f"ERROR rotating log: {e}")
        status = Status.FAIL

    # Prune old rotated logs
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    pruned = 0
    for f in data_dir.glob("memory_replication.*.log"):
        try:
            date_str = f.stem.split(".", 1)[1]  # "memory_replication.YYYY-MM-DD" → "YYYY-MM-DD"
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_date < cutoff:
                f.unlink()
                pruned += 1
        except Exception:
            continue

    if pruned:
        lines.append(f"Pruned {pruned} old rotated log(s) (>{KEEP_DAYS}d)")

    if not quiet:
        print(f"[replication_log_rotate] {status.value} — rotated to {rotated.name}, pruned {pruned}")

    return status, "\n".join(lines)


def main():
    quiet = "--quiet" in sys.argv
    status, content = run_rotate(quiet=quiet)
    write_report("replication_log_rotate.md", content, status)
    sys.exit(status_to_exit_code(status))


if __name__ == "__main__":
    main()
