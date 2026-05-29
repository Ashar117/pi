#!/usr/bin/env python3
"""scripts/retention_tick.py — T-112: Run Pi retention policies.

Called by cron / Task Scheduler daily. Exits 0 on success, 1 on any error.

Usage:
    python scripts/retention_tick.py
    python scripts/retention_tick.py --dry-run
    python scripts/retention_tick.py --policies turns_jsonl,evolution_jsonl

Scheduling (run once, at ~03:00 local):
  Windows:  schtasks /create /tn "Pi Retention" /tr "python E:\\pi\\scripts\\retention_tick.py" /sc DAILY /st 03:00
  Unix/Mac: 0 3 * * * cd /path/to/pi && python scripts/retention_tick.py
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.retention import run_all, DEFAULT_POLICIES


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Pi retention policies")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate policy execution without mutating any files",
    )
    parser.add_argument(
        "--policies",
        metavar="NAME1,NAME2",
        default="",
        help="Comma-separated policy names to run (default: all)",
    )
    args = parser.parse_args()

    policies = DEFAULT_POLICIES
    if args.policies:
        names = {n.strip() for n in args.policies.split(",") if n.strip()}
        policies = [p for p in DEFAULT_POLICIES if p.name in names]
        if not policies:
            print(f"[retention] No matching policies for: {args.policies}", flush=True)
            return 1

    if args.dry_run:
        print("[retention] DRY RUN — no files will be modified", flush=True)

    summary = run_all(policies, dry_run=args.dry_run)
    applied = summary["applied"]
    errors = summary["errors"]
    total = summary["policies_run"]

    for detail in summary["details"]:
        status = "APPLIED" if detail["applied"] else "SKIP"
        reason = detail.get("reason", "")
        dur = detail.get("duration_s", 0)
        name = detail["name"]
        print(f"  [{status}] {name}: {reason} ({dur:.3f}s)", flush=True)

    print(f"\n[retention] {total} policies: {applied} applied, {errors} errors", flush=True)

    # T-125a/T-125b/T-125c: piggy-back caretaker on the daily retention tick.
    # full() = lite() + dedup + contradictions; deep() = Haiku pattern review.
    # Failures never change retention's exit code.
    if not args.dry_run:
        try:
            from agent.caretaker import full as _caretaker_full, deep as _caretaker_deep
            from pathlib import Path as _Path
            db_path = _Path(__file__).parent.parent / "data" / "pi.db"
            stats = _caretaker_full(db_path)
            print(
                f"[caretaker] full: recomputed={stats['recomputed']} "
                f"deduped={stats['deduped']} "
                f"contradictions_invalidated={stats.get('contradictions_invalidated', 0)} "
                f"errors={stats['errors']+stats['dedup_errors']}",
                flush=True,
            )
            # Deep review — nightly only, never on session-exit
            deep_stats = _caretaker_deep(db_path)
            print(
                f"[caretaker] deep: categories_reviewed={deep_stats['categories_reviewed']}",
                flush=True,
            )
        except Exception as e:
            print(f"[caretaker] full/deep failed (non-fatal): {e}", flush=True)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
