#!/usr/bin/env python3
"""
testing/evals/runner.py — T-209: Behavioral eval harness.

Loads scenario files from testing/evals/scenarios.json, runs deterministic
checks against scripted conversations, and appends scores to logs/evals.jsonl.

Designed for OFFLINE use — checks are substring/structural, no LLM judge.
Live variant (--live) runs against real cheap-tier API but is COSTLY_TESTS class.

USAGE
-----
    python testing/evals/runner.py                   # offline, all scenarios
    python testing/evals/runner.py --scenario EVAL-001  # single scenario
    python testing/evals/runner.py --list            # show all scenario IDs
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

SCENARIOS_PATH = Path(__file__).parent / "scenarios.json"
EVALS_LOG = ROOT / "logs" / "evals.jsonl"


# ── Scenario loading ──────────────────────────────────────────────────────────

def load_scenarios(path: Path = SCENARIOS_PATH) -> List[Dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


# ── Check evaluation ──────────────────────────────────────────────────────────

def run_check(check: Dict, response: str, tool_calls: List[str]) -> Tuple[bool, str]:
    """Evaluate a single check. Returns (passed, reason)."""
    ctype = check.get("type", "")
    value = check.get("value", "")
    msg = check.get("message", f"check {ctype}={value!r}")

    if ctype == "must_contain":
        ok = value.lower() in response.lower()
        return ok, "" if ok else f"FAIL: {msg} — '{value}' not found in response"
    elif ctype == "must_not_contain":
        ok = value.lower() not in response.lower()
        return ok, "" if ok else f"FAIL: {msg} — '{value}' found in response"
    elif ctype == "must_call_tool":
        ok = value in tool_calls
        return ok, "" if ok else f"FAIL: {msg} — tool '{value}' not called"
    elif ctype == "must_not_call_tool":
        ok = value not in tool_calls
        return ok, "" if ok else f"FAIL: {msg} — tool '{value}' was called but shouldn't be"
    else:
        return True, f"SKIP: unknown check type '{ctype}'"


def score_scenario(scenario: Dict, response: str, tool_calls: List[str]) -> Dict:
    """Run all checks and return a score dict."""
    checks = scenario.get("checks", [])
    if not checks:
        return {"scenario_id": scenario["id"], "checks": 0, "passed": 0, "failed": 0,
                "skipped": 0, "failures": []}

    results = [run_check(c, response, tool_calls) for c in checks]
    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, msg in results if not ok and not msg.startswith("SKIP"))
    skipped = sum(1 for _, msg in results if msg.startswith("SKIP"))
    failures = [msg for ok, msg in results if not ok and not msg.startswith("SKIP")]

    return {
        "scenario_id": scenario["id"],
        "checks": len(checks),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "failures": failures,
    }


# ── Offline runner ────────────────────────────────────────────────────────────

def run_offline(scenario: Dict) -> Dict:
    """
    Offline eval: evaluate checks against a fixture response.

    For scenarios without provider_fixture in the last turn, uses an empty
    response string (tests that must_not_contain checks pass on empty).
    """
    turns = scenario.get("turns", [])
    response = ""
    tool_calls: List[str] = []

    # Use the last user turn's fixture if available
    for turn in reversed(turns):
        if turn.get("role") == "user" and turn.get("provider_fixture"):
            response = turn["provider_fixture"]
            break

    # Gather any tool calls defined in fixtures
    for turn in turns:
        if turn.get("role") == "assistant_fixture":
            content = turn.get("content", "")
            if "tool_use" in content:
                # Extract tool name from simple fixture format "TOOL:name"
                for part in content.split():
                    if part.startswith("TOOL:"):
                        tool_calls.append(part[5:])

    score = score_scenario(scenario, response, tool_calls)
    score["mode"] = "offline"
    return score


# ── Logging ───────────────────────────────────────────────────────────────────

def log_results(results: List[Dict]) -> None:
    EVALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "scenarios": len(results),
        "total_checks": sum(r["checks"] for r in results),
        "total_passed": sum(r["passed"] for r in results),
        "total_failed": sum(r["failed"] for r in results),
        "results": results,
    }
    with EVALS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Behavioral eval runner (T-209).")
    ap.add_argument("--scenario", type=str, default=None,
                    help="Run a single scenario by ID.")
    ap.add_argument("--list", action="store_true",
                    help="List all scenario IDs and exit.")
    ap.add_argument("--log", action="store_true",
                    help="Append results to logs/evals.jsonl.")
    args = ap.parse_args()

    scenarios = load_scenarios()
    if not scenarios:
        print("[evals] No scenarios found.")
        return 1

    if args.list:
        for s in scenarios:
            print(f"  {s['id']}: {s['title']}")
        return 0

    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]
        if not scenarios:
            print(f"[evals] Scenario {args.scenario!r} not found.")
            return 1

    results = []
    total_passed = total_failed = 0
    for scenario in scenarios:
        if not scenario.get("checks"):
            print(f"  SKIP {scenario['id']}: no checks defined")
            continue
        result = run_offline(scenario)
        results.append(result)
        total_passed += result["passed"]
        total_failed += result["failed"]
        status = "PASS" if result["failed"] == 0 else "FAIL"
        print(f"  [{status}] {result['scenario_id']}: {result['passed']}/{result['checks']} checks")
        for failure in result["failures"]:
            print(f"         {failure}")

    print(f"\n[evals] Total: {total_passed} passed, {total_failed} failed "
          f"({len(results)} scenarios run)")

    if args.log and results:
        log_results(results)
        print(f"[evals] Results logged to {EVALS_LOG.relative_to(ROOT)}")

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
