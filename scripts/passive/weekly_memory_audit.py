"""scripts/passive/weekly_memory_audit.py — weekly memory audit (T-085 R4).

Action script (not a checker): runs memory.audit.run_audit() and writes the
digest to vault/notes/memory/audit/YYYY-Www.md via render_audit_digest().
Replaces the in-exit audit block per ADR-005 so the audit runs on a
predictable weekly schedule independent of session boundaries.

Registered as a weekly Sunday 02:00 PiScheduler job. Can also be run
manually:

    python scripts/passive/weekly_memory_audit.py            # respect should_run_weekly gate
    python scripts/passive/weekly_memory_audit.py --force    # ignore the gate, run now
    python scripts/passive/weekly_memory_audit.py --quiet    # no stdout

Exit codes:
    0 — audit ran successfully (or was skipped by the should_run_weekly gate)
    1 — audit ran with non-fatal errors recorded in audit_run.errors
    2 — failed to construct the memory layer or fatal import error

The in-daemon PiScheduler job and this script call the same `run_audit`
function — invocation path doesn't affect output. Telegram notification
is wired in the scheduler job; the standalone CLI prints to stdout only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))


def _build_memory():
    from tools.tools_memory import MemoryTools
    try:
        from app.config import SUPABASE_URL, SUPABASE_KEY
    except Exception as e:
        raise SystemExit(f"[weekly_audit] config import failed: {e}")
    return MemoryTools(SUPABASE_URL, SUPABASE_KEY)


def main(force: bool = False, quiet: bool = False) -> int:
    try:
        from memory.audit import run_audit, should_run_weekly
        from tools.tools_obsidian import render_audit_digest, _default_vault_root
    except Exception as e:
        print(f"[weekly_audit] import failed: {e}", file=sys.stderr)
        return 2

    if not force and not should_run_weekly():
        if not quiet:
            print("[weekly_audit] skipped — should_run_weekly()=False. Use --force to override.")
        return 0

    try:
        mem = _build_memory()
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        audit_run = run_audit(mem)
        res = render_audit_digest(audit_run, _default_vault_root())
    except Exception as e:
        print(f"[weekly_audit] audit run failed: {e}", file=sys.stderr)
        return 2

    if not quiet:
        print(f"[weekly_audit] week={audit_run.week_iso}")
        print(f"[weekly_audit] findings: flagged={len(audit_run.flagged)} "
              f"archived={len(audit_run.archived)} deleted={len(audit_run.deleted)} "
              f"merge_candidates={len(audit_run.merge_suggestions)}")
        print(f"[weekly_audit] digest: {res.get('path')}")
        if audit_run.errors:
            print(f"[weekly_audit] WARN: {len(audit_run.errors)} non-fatal errors during audit")

    return 1 if audit_run.errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="Ignore should_run_weekly() gate; run anyway.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    sys.exit(main(force=args.force, quiet=args.quiet))
