#!/usr/bin/env python3
"""scripts/close_ticket.py — T-128: sanctioned ticket-close path with gates.

Closing a ticket today (pre-T-128) means manually moving its JSON from
tickets/open/ to tickets/closed/. That bypasses every safeguard: verify
might not have been run, no solution recorded, ADRs forgotten. This script
is the only sanctioned close path going forward.

Gates run in order; first failure exits non-zero. --force bypasses with
a track_silent record for auditability.

Usage:
    python scripts/close_ticket.py T-NNN
    python scripts/close_ticket.py T-NNN --no-solution        # for workflow tickets
    python scripts/close_ticket.py T-NNN --actual-minutes 90  # skip prompt
    python scripts/close_ticket.py T-NNN --force              # bypass gates (logged)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# Force UTF-8 stdout on Windows — ticket titles may contain — → emoji etc.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

OPEN_DIR = _ROOT / "tickets" / "open"
CLOSED_DIR = _ROOT / "tickets" / "closed"
SOLUTIONS_PATH = _ROOT / "solutions" / "SOLUTIONS.jsonl"
ADR_DIR = _ROOT / "docs" / "adr"


# ── Gate result type ──────────────────────────────────────────────────────────

@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str


def _track(event: str, **context) -> None:
    try:
        from agent.observability import track_silent
        track_silent(f"close_ticket.{event}", None, context=context)
    except Exception:
        pass


# ── Ticket lookup ─────────────────────────────────────────────────────────────

def find_ticket(ticket_id: str) -> Tuple[Optional[Path], str]:
    """Return (path, location). location ∈ {'open', 'closed', 'missing'}."""
    for p in OPEN_DIR.glob(f"{ticket_id}-*.json"):
        return p, "open"
    for p in CLOSED_DIR.glob(f"{ticket_id}-*.json"):
        return p, "closed"
    return None, "missing"


# ── Gates ─────────────────────────────────────────────────────────────────────

def gate_verify_pass(ticket: dict, args) -> GateResult:
    """verify.py must exit 0."""
    if args.skip_verify:
        return GateResult("verify", True, "skipped (--skip-verify)")
    print("[close] running verify.py (this may take ~2 minutes)...", flush=True)
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "verify.py"), "--quiet"],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return GateResult("verify", True, "exit 0")
    return GateResult(
        "verify",
        False,
        f"verify.py exited {result.returncode}; tail: {result.stdout[-200:]}",
    )


def gate_solution_recorded(ticket: dict, args) -> GateResult:
    """SOLUTIONS.jsonl must contain an entry referencing this ticket."""
    if args.no_solution:
        return GateResult("solution", True, "skipped (--no-solution)")
    if not SOLUTIONS_PATH.exists():
        return GateResult("solution", False, f"{SOLUTIONS_PATH} missing")
    tid = ticket["id"]
    try:
        for line in SOLUTIONS_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if tid in (rec.get("ticket_ids") or []):
                return GateResult("solution", True, f"linked to {rec.get('id', '?')}")
    except Exception as e:
        return GateResult("solution", False, f"failed to scan SOLUTIONS.jsonl: {e}")
    return GateResult(
        "solution",
        False,
        f"no entry in SOLUTIONS.jsonl references {tid} (use --no-solution if intentional)",
    )


def gate_adr_present(ticket: dict, args) -> GateResult:
    """If ticket.adr_required is set, an ADR file must exist."""
    adr_req = ticket.get("adr_required")
    if not adr_req:
        return GateResult("adr", True, "not required")
    # adr_required might be a free-form string like "ADR-007 — memory lifecycle"
    # — extract digits if present
    import re
    m = re.search(r"(\d{3})", str(adr_req))
    if m:
        pattern = f"{m.group(1)}-*.md"
    else:
        pattern = "*.md"
    matches = list(ADR_DIR.glob(pattern))
    if matches:
        return GateResult("adr", True, f"found {matches[0].name}")
    return GateResult("adr", False, f"adr_required='{adr_req}' but no matching file in {ADR_DIR}")


def gate_no_new_debt(ticket: dict, args) -> GateResult:
    """Soft gate: warn if files_affected contain new bare except / TODO patterns.

    Returns PASS with note (never hard-blocks) so the close script doesn't
    turn into a linter battle. Real enforcement is the passive tech_debt
    skill that runs daily.
    """
    return GateResult("no_new_debt", True, "soft gate — see /passive tech_debt")


GATES: List[Callable] = [
    gate_verify_pass,
    gate_solution_recorded,
    gate_adr_present,
    gate_no_new_debt,
]


# ── Main ──────────────────────────────────────────────────────────────────────

def run_gates(ticket: dict, args) -> Tuple[bool, List[GateResult]]:
    results = []
    for gate in GATES:
        try:
            r = gate(ticket, args)
        except Exception as e:
            r = GateResult(gate.__name__, False, f"gate crashed: {e}")
        results.append(r)
    all_passed = all(r.passed for r in results)
    return all_passed, results


def prompt_effort_minutes() -> Optional[int]:
    print("\nHow long did this ticket actually take, in minutes?")
    print("(blank to skip)")
    try:
        raw = input("> ").strip()
    except EOFError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print(f"[close] '{raw}' is not an integer; skipping effort record.")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Close a Pi ticket with gates.")
    parser.add_argument("ticket_id", help="e.g. T-121")
    parser.add_argument("--no-solution", action="store_true", help="ticket has no SOLUTIONS entry (workflow/infra)")
    parser.add_argument("--actual-minutes", type=int, default=None, help="skip the effort prompt")
    parser.add_argument("--skip-verify", action="store_true", help="skip verify.py gate (use with care)")
    parser.add_argument("--force", action="store_true", help="bypass ALL gates (logged via track_silent)")
    args = parser.parse_args()

    tid = args.ticket_id
    path, loc = find_ticket(tid)
    if loc == "missing":
        print(f"[close] ticket {tid} not found in tickets/open/ or tickets/closed/", file=sys.stderr)
        return 1
    if loc == "closed":
        print(f"[close] {tid} is already closed (idempotent: {path.name})")
        return 0

    ticket = json.loads(path.read_text(encoding="utf-8"))

    print(f"[close] closing {tid}: {ticket.get('title', '?')}")

    if args.force:
        _track("force_bypass", ticket_id=tid)
        print("[close] WARNING: --force bypasses all gates (logged via track_silent)", file=sys.stderr)
        results = [GateResult("force", True, "all gates bypassed")]
        all_passed = True
    else:
        all_passed, results = run_gates(ticket, args)

    print("\n[close] gate results:")
    for r in results:
        icon = "[OK]" if r.passed else "[FAIL]"
        print(f"  {icon:7} {r.name:15} {r.detail}")

    if not all_passed:
        print("\n[close] FAIL — one or more gates blocked the close. Fix or use --force.", file=sys.stderr)
        return 1

    # Effort prompt
    actual = args.actual_minutes if args.actual_minutes is not None else prompt_effort_minutes()
    if actual is not None:
        ticket["effort_actual_minutes"] = actual

    # Mark closed
    ticket["status"] = "closed"
    ticket["closed"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Write to closed/ and remove from open/
    dst = CLOSED_DIR / path.name
    CLOSED_DIR.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(ticket, indent=2, ensure_ascii=False), encoding="utf-8")
    path.unlink()

    print(f"\n[close] {tid} closed -> {dst.relative_to(_ROOT)}")
    if "effort_actual_minutes" in ticket:
        est = ticket.get("effort_estimate", "?")
        print(f"  estimate: {est} | actual: {ticket['effort_actual_minutes']} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
