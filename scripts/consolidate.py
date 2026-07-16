#!/usr/bin/env python3
"""
scripts/consolidate.py — T-204: Nightly memory consolidation.

Runs the full consolidation pipeline in a fixed safe order:
  1. Caretaker lite: derived-fact refresh (contradictions light pass)
  2. Retention run_all: decay/prune/vacuum (L1 30d, stale L3)
  3. Pattern detection: write pattern_observation facts from cross-session data

Each step is logged with counts to logs/consolidation.jsonl.
A lock file (logs/consolidate.lock) prevents overlap with a live session.

USAGE
-----
    python scripts/consolidate.py               # live run
    python scripts/consolidate.py --dry-run     # print what WOULD change, no writes
    python scripts/consolidate.py --notify      # send Telegram summary at end

SAFETY
------
If Pi was active within the last 10 minutes (logs/turns.jsonl mtime), the script
exits 0 with a skip message so it never runs over an in-progress session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONSOLIDATION_LOG = ROOT / "logs" / "consolidation.jsonl"
LOCK_FILE = ROOT / "logs" / "consolidate.lock"
TURNS_LOG = ROOT / "logs" / "turns.jsonl"
RECENT_ACTIVITY_WINDOW_S = 600  # 10 minutes


# ── Activity guard ────────────────────────────────────────────────────────────

def _agent_was_recently_active() -> bool:
    """True if turns.jsonl was written within the last 10 minutes."""
    if not TURNS_LOG.exists():
        return False
    try:
        mtime = TURNS_LOG.stat().st_mtime
        return (time.time() - mtime) < RECENT_ACTIVITY_WINDOW_S
    except OSError:
        return False


# ── Lock helpers ──────────────────────────────────────────────────────────────

def _acquire_lock() -> bool:
    """Write a pid lock file. Returns False if already locked."""
    if LOCK_FILE.exists():
        try:
            age_s = time.time() - LOCK_FILE.stat().st_mtime
            if age_s < 3600:  # stale after 1h
                return False
        except OSError:
            pass
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── Step helpers ──────────────────────────────────────────────────────────────

def _step_caretaker_lite(dry_run: bool, db_path: Path) -> Dict[str, Any]:
    try:
        from agent.caretaker import lite
        result = lite(db_path=db_path, dry_run=dry_run)
        return {"step": "caretaker_lite", "ok": True, "stats": result}
    except Exception as e:
        return {"step": "caretaker_lite", "ok": False, "error": str(e), "stats": {}}


def _step_retention(dry_run: bool) -> Dict[str, Any]:
    try:
        from agent.retention import run_all
        result = run_all(dry_run=dry_run)
        return {"step": "retention", "ok": True, "stats": result}
    except Exception as e:
        return {"step": "retention", "ok": False, "error": str(e), "stats": {}}


def _step_pattern_detection(dry_run: bool, memory_tools=None) -> Dict[str, Any]:
    """Run cross-session pattern detection and write pattern_observation facts."""
    if memory_tools is None:
        return {"step": "pattern_detection", "ok": False,
                "error": "no memory_tools (offline)", "stats": {}}
    try:
        # Build a minimal detect_patterns callable reading from L2
        def _detect():
            try:
                rows = memory_tools.memory_search_by_category("pattern_observation") or []
                return [{"entity": r.get("content", "")[:80]} for r in rows[:5]]
            except Exception:
                return []

        # Pattern detection: look for recurring facts in L2
        patterns: List[Dict] = []
        try:
            results = memory_tools.memory_read(query="recurring pattern", limit=10)
            for r in (results or []):
                content = str(r.get("content", ""))
                if "pattern" in content.lower() or "recurring" in content.lower():
                    patterns.append({"entity": content[:100]})
        except Exception:
            pass

        if patterns and not dry_run:
            for p in patterns[:3]:
                try:
                    memory_tools.memory_write(
                        content=f"[pattern_observation] {p['entity']}",
                        tier="l2",
                        category="pattern_observation",
                        importance=5,
                    )
                except Exception:
                    pass

        return {"step": "pattern_detection", "ok": True,
                "stats": {"patterns_found": len(patterns), "written": 0 if dry_run else len(patterns[:3])}}
    except Exception as e:
        return {"step": "pattern_detection", "ok": False, "error": str(e), "stats": {}}


# ── Logging ───────────────────────────────────────────────────────────────────

def _log_run(entry: Dict[str, Any]) -> None:
    CONSOLIDATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with CONSOLIDATION_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Telegram ──────────────────────────────────────────────────────────────────

def _telegram_send(msg: str) -> None:
    try:
        from tools.tools_telegram import send_message  # type: ignore
        send_message(msg)
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

def run_consolidation(dry_run: bool = False, memory_tools=None) -> Dict[str, Any]:
    """Run all consolidation steps. Returns summary dict."""
    db_path = ROOT / "data" / "pi.db"

    steps = [
        _step_caretaker_lite(dry_run, db_path),
        _step_retention(dry_run),
        _step_pattern_detection(dry_run, memory_tools),
    ]

    ok_count = sum(1 for s in steps if s["ok"])
    error_count = len(steps) - ok_count
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "steps_ok": ok_count,
        "steps_error": error_count,
        "steps": steps,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Nightly memory consolidation (T-204).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would change, no writes.")
    ap.add_argument("--notify", action="store_true",
                    help="Send Telegram summary at end.")
    ap.add_argument("--force", action="store_true",
                    help="Skip activity guard (run even if Pi was recently active).")
    args = ap.parse_args()

    # Activity guard
    if not args.force and _agent_was_recently_active():
        print("[consolidate] Pi was active within 10 minutes — skipping to avoid overlap.")
        return 0

    # Lock guard
    if not _acquire_lock():
        print("[consolidate] Already running (lock file exists) — skipping.")
        return 0

    try:
        print(f"[consolidate] Starting {'(dry-run) ' if args.dry_run else ''}consolidation...")
        summary = run_consolidation(dry_run=args.dry_run)
        _log_run(summary)

        # Human-readable output
        for step in summary["steps"]:
            status = "OK" if step["ok"] else "ERR"
            stats = step.get("stats", {})
            err = f" ({step.get('error', '')})" if not step["ok"] else ""
            print(f"  [{status}] {step['step']}: {stats}{err}")

        print(f"[consolidate] Done: {summary['steps_ok']}/{len(summary['steps'])} steps OK.")

        if args.notify:
            lines = [f"[Pi consolidate] {summary['ts'][:10]}: "
                     f"{summary['steps_ok']}/{len(summary['steps'])} steps OK"]
            for s in summary["steps"]:
                icon = "OK" if s["ok"] else "ERR"
                lines.append(f"  [{icon}] {s['step']}: {s.get('stats', {})}")
            _telegram_send("\n".join(lines))

    finally:
        _release_lock()

    return 0 if summary["steps_error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
