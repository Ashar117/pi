"""
scripts/passive/tool_usage_audit.py — SKILL: tool-usage audit (T-083 R2.3)

Reads logs/patterns.jsonl to surface:
  1. Under-used tools (<3 invocations in 30d) → auto-file P3 prune ticket
  2. High-failure tools (>50% failure rate, >10 invocations) → auto-file P2 fix ticket

Output: reports/tool_usage_audit.md
Exits: 0=PASS  1=WARN  2=FAIL

CLI:
  python scripts/passive/tool_usage_audit.py          # standard check
  python scripts/passive/tool_usage_audit.py --strict # treat WARN as FAIL
  python scripts/passive/tool_usage_audit.py --quiet  # suppress stdout
  python scripts/passive/tool_usage_audit.py --dry-run # do not write tickets
"""

from __future__ import annotations

import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    REPORTS,
    TICKETS_OPEN,
    Status,
    write_report,
    status_to_exit_code,
    worst,
)

# ── thresholds ────────────────────────────────────────────────────────────────

WINDOW_DAYS    = 30
PRUNE_MIN      = 3       # < this → P3 prune candidate
FAIL_MIN_CALLS = 10      # require >= this before flagging failure rate
FAIL_THRESHOLD = 0.50    # > this → P2 fix ticket


# ── data loading ──────────────────────────────────────────────────────────────

def _load_pattern_stats(root: Path) -> Dict[str, Dict]:
    """Return {tool_name: {calls, failures}} from logs/patterns.jsonl (last WINDOW_DAYS)."""
    patterns_path = root / "logs" / "patterns.jsonl"
    if not patterns_path.exists():
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    stats: Dict[str, Dict] = defaultdict(lambda: {"calls": 0, "failures": 0})

    for line in patterns_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        pattern = rec.get("pattern", "")
        if not pattern.startswith("tool_"):
            continue
        try:
            ts = datetime.fromisoformat(rec.get("timestamp", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        tool_name = pattern[len("tool_"):]
        stats[tool_name]["calls"] += 1
        if not rec.get("success", True):
            stats[tool_name]["failures"] += 1

    return dict(stats)


def _get_registered_tools() -> List[str]:
    """Return canonical tool names from the live registry."""
    try:
        import agent.tools as at
        at._REGISTRY_CACHE = None
        return [d["name"] for d in at.get_tool_definitions()]
    except Exception:
        return []


def _file_ticket(title: str, severity: str, body: dict, dry_run: bool) -> str:
    ticket_id = f"T-AUDIT-{uuid.uuid4().hex[:6].upper()}"
    ticket = {
        "id": ticket_id,
        "source": "tool_usage_audit.py",
        "title": title,
        "severity": severity,
        "status": "open",
        "created": datetime.now(timezone.utc).isoformat(),
        **body,
    }
    if not dry_run:
        path = TICKETS_OPEN / f"{ticket_id}-auto-audit.json"
        path.write_text(json.dumps(ticket, indent=2), encoding="utf-8")
    return ticket_id


# ── main audit ────────────────────────────────────────────────────────────────

def run_audit(root: Path = _DEFAULT_ROOT, strict: bool = False,
              quiet: bool = False, dry_run: bool = False):
    sections: List[str] = []
    statuses: List[Status] = []

    stats      = _load_pattern_stats(root)
    registered = _get_registered_tools()

    # ── 1. Under-used ──────────────────────────────────────────────────────────
    prune = [
        (n, stats.get(n, {}).get("calls", 0))
        for n in registered
        if stats.get(n, {}).get("calls", 0) < PRUNE_MIN
    ]
    sections.append(f"## Under-used tools (< {PRUNE_MIN} calls / {WINDOW_DAYS}d)")
    if prune:
        statuses.append(Status.WARN)
        rows = []
        for name, calls in sorted(prune, key=lambda x: x[1]):
            rows.append(f"- `{name}`: {calls} calls")
            _file_ticket(
                title=f"[auto] Prune tool `{name}` — {calls} calls in {WINDOW_DAYS}d",
                severity="P3",
                body={
                    "tool": name,
                    "calls_30d": calls,
                    "threshold": PRUNE_MIN,
                    "suggested_fix": (
                        f"Remove `{name}` from the tool registry or merge it "
                        f"({calls} invocations / {WINDOW_DAYS}d — planner cost with no value)."
                    ),
                },
                dry_run=dry_run,
            )
        sections.append("\n".join(rows))
    else:
        sections.append("None — all registered tools used above threshold.")

    # ── 2. High-failure ────────────────────────────────────────────────────────
    high_fail = [
        (n, s["calls"], s["failures"])
        for n, s in stats.items()
        if s["calls"] >= FAIL_MIN_CALLS
        and s["failures"] / s["calls"] > FAIL_THRESHOLD
    ]
    sections.append(f"\n## High-failure tools (> {int(FAIL_THRESHOLD*100)}% / >= {FAIL_MIN_CALLS} calls)")
    if high_fail:
        statuses.append(Status.WARN)
        rows = []
        for name, calls, failures in sorted(high_fail, key=lambda x: -x[2] / x[1]):
            rate = round(failures / calls * 100)
            rows.append(f"- `{name}`: {failures}/{calls} failures ({rate}%)")
            _file_ticket(
                title=f"[auto] High failure rate `{name}` — {rate}% in {WINDOW_DAYS}d",
                severity="P2",
                body={
                    "tool": name,
                    "calls_30d": calls,
                    "failures_30d": failures,
                    "failure_rate_pct": rate,
                    "suggested_fix": (
                        f"Investigate `{name}` failures ({rate}% over {calls} calls). "
                        "Check logs/patterns.jsonl for error context."
                    ),
                },
                dry_run=dry_run,
            )
        sections.append("\n".join(rows))
    else:
        sections.append(f"None — no tool exceeded {int(FAIL_THRESHOLD*100)}% failure rate.")

    # ── Summary ────────────────────────────────────────────────────────────────
    final = worst(statuses) if statuses else Status.PASS
    if strict and final == Status.WARN:
        final = Status.FAIL

    summary = (
        f"\n## Summary\n"
        f"- Registered tools: {len(registered)}\n"
        f"- Prune candidates: {len(prune)}\n"
        f"- High-failure: {len(high_fail)}\n"
        f"- Status: **{final.value}**"
    )
    sections.append(summary)

    content = "\n".join(sections)
    if not quiet:
        print(f"[tool_usage_audit] {final.value} — "
              f"{len(prune)} prune candidates, {len(high_fail)} high-fail")

    return final, content


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    strict  = "--strict"  in sys.argv
    quiet   = "--quiet"   in sys.argv
    dry_run = "--dry-run" in sys.argv

    status, content = run_audit(strict=strict, quiet=quiet, dry_run=dry_run)
    write_report("tool_usage_audit.md", content, status)
    sys.exit(status_to_exit_code(status))


if __name__ == "__main__":
    main()
