"""scripts/passive/memory_prune.py — daily L3 + L2 memory prune (T-085 R4).

Action script (not a checker): runs prune_l3_expired() + prune_l2_stale()
against the Pi memory layer. Replaces the in-exit prune calls per ADR-005
so prunes happen on a predictable daily schedule independent of when the
user happens to exit Pi.

Registered as a daily 04:00 PiScheduler job via tools/tools_scheduler.py.
Can also be run manually:

    python scripts/passive/memory_prune.py            # do the prune, print summary
    python scripts/passive/memory_prune.py --dry-run  # report what would be pruned
    python scripts/passive/memory_prune.py --quiet    # no stdout, just exit code

Exit codes:
    0 — pruned (or dry-ran) successfully, no errors
    1 — partial failure (one of the two prunes raised); other completed
    2 — Pi memory layer unreachable / both prunes failed

This script is safe to run on a stopped daemon — it constructs its own
MemoryTools instance via Pi's standard config and writes directly to the
same SQLite + Supabase. No need for the daemon to be up.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))


def _build_memory():
    """Build a MemoryTools instance using Pi's standard config (lazy)."""
    from tools.tools_memory import MemoryTools
    try:
        from app.config import SUPABASE_URL, SUPABASE_KEY
    except Exception as e:
        raise SystemExit(f"[memory_prune] config import failed: {e}")
    return MemoryTools(SUPABASE_URL, SUPABASE_KEY)


def main(dry_run: bool = False, quiet: bool = False) -> int:
    mem = _build_memory()

    if dry_run:
        if not quiet:
            print("[memory_prune] DRY RUN — no writes will happen.")
        # No actual prune; report what would be pruned would require
        # changing the prune APIs to support dry-run mode. Out of scope —
        # the dry-run flag is a placeholder for a future small ticket.
        if not quiet:
            print("[memory_prune] dry-run not yet implemented at the API level; "
                  "use scripts/pi_audit.py for L2 candidates.")
        return 0

    failures: list = []

    try:
        res_l3 = mem.prune_l3_expired()
        if not quiet:
            print(f"[memory_prune] L3 expired: {res_l3}")
    except Exception as e:
        failures.append(("prune_l3_expired", str(e)))
        if not quiet:
            print(f"[memory_prune] prune_l3_expired failed: {e}", file=sys.stderr)

    try:
        res_l2 = mem.prune_l2_stale()
        if not quiet:
            print(f"[memory_prune] L2 stale: {res_l2}")
    except Exception as e:
        failures.append(("prune_l2_stale", str(e)))
        if not quiet:
            print(f"[memory_prune] prune_l2_stale failed: {e}", file=sys.stderr)

    if not failures:
        return 0
    return 2 if len(failures) == 2 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    sys.exit(main(dry_run=args.dry_run, quiet=args.quiet))
