"""
scripts/passive/memory_pollution_detector.py — SKILL 12

Passive memory pollution detector.  Inspects Pi's memory layers for
quality issues: stale entries, missing timestamps, duplicate keys,
oversized values, and vault note health.
NEVER modifies memory stores.

Checks:
  1. L1 JSON memory        — stale / oversized / missing timestamp
  2. Vault notes health    — missing frontmatter, empty notes
  3. Memory density        — WARN if total stored entries exceed DENSITY_LIMIT

Output: reports/memory_pollution_detector.md

CLI:
  python scripts/passive/memory_pollution_detector.py --check
  python scripts/passive/memory_pollution_detector.py --strict
  python scripts/passive/memory_pollution_detector.py --quiet
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    REPORTS as _DEFAULT_REPORTS,
    Status,
    write_report,
    status_to_exit_code,
    worst,
)

REPORT_FILE      = "memory_pollution_detector.md"

STALE_DAYS       = 90     # L1 entry not updated in N days = stale
MAX_VALUE_CHARS  = 4000   # L1 value longer than this = oversized
DENSITY_LIMIT    = 500    # total L1 entries before WARN
EMPTY_NOTE_BYTES = 50     # vault note smaller than this is considered empty


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _read_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ── Individual checks ─────────────────────────────────────────────────────────

def check_l1_memory(root: Path) -> Tuple[Status, List[str]]:
    """Scan memory/l1.json (or memory/*.json) for staleness/size issues."""
    mem_dir = root / "memory"
    if not mem_dir.exists():
        return Status.PASS, ["[ok] No memory/ dir found — skipped"]

    json_files = list(mem_dir.glob("*.json"))
    if not json_files:
        return Status.PASS, ["[ok] No JSON memory files found"]

    stale: List[str] = []
    oversized: List[str] = []
    no_ts: List[str] = []
    total_entries = 0
    cutoff = _now_utc() - timedelta(days=STALE_DAYS)

    for jf in sorted(json_files):
        try:
            data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue

        if isinstance(data, dict):
            items = data.items()
        elif isinstance(data, list):
            items = enumerate(data)
        else:
            continue

        for key, val in items:
            total_entries += 1
            val_str = json.dumps(val) if not isinstance(val, str) else val

            # Oversized check
            if len(val_str) > MAX_VALUE_CHARS:
                oversized.append(f"{jf.name}[{key}] ({len(val_str)} chars)")

            # Timestamp / staleness
            if isinstance(val, dict):
                ts_raw = val.get("updated_at") or val.get("timestamp") or val.get("created_at")
                if not ts_raw:
                    no_ts.append(f"{jf.name}[{key}]")
                else:
                    dt = _parse_dt(str(ts_raw))
                    if dt and dt < cutoff:
                        days = (_now_utc() - dt).days
                        stale.append(f"{jf.name}[{key}] ({days}d old)")

    issues: List[str] = []
    status = Status.PASS

    if stale:
        issues.append(
            f"[warn] {len(stale)} stale L1 entry/entries (>{STALE_DAYS}d old):"
        )
        for s in stale[:5]:
            issues.append(f"  - {s}")
        status = Status.WARN

    if oversized:
        issues.append(
            f"[warn] {len(oversized)} oversized entry/entries (>{MAX_VALUE_CHARS} chars):"
        )
        for s in oversized[:5]:
            issues.append(f"  - {s}")
        status = Status.WARN

    if no_ts:
        issues.append(
            f"[warn] {len(no_ts)} entries missing timestamp fields:"
        )
        for s in no_ts[:5]:
            issues.append(f"  - {s}")
        status = worst([status, Status.WARN])

    if not issues:
        issues = [
            f"[ok] {total_entries} L1 memory entries — no staleness or size issues"
        ]

    return status, issues


def check_vault_notes(root: Path) -> Tuple[Status, List[str]]:
    """Check vault notes for missing frontmatter and empty content.

    Any `.god`-style private vault subdir is excluded — a passive report must
    never surface a private layer's filenames or content.
    """
    vault_dir = root / "vault"
    if not vault_dir.exists():
        return Status.PASS, ["[ok] No vault/ dir found — skipped"]

    md_files = [p for p in vault_dir.rglob("*.md") if ".god" not in p.parts]
    if not md_files:
        return Status.PASS, ["[ok] No vault markdown notes found"]

    no_frontmatter: List[str] = []
    empty_notes: List[str] = []

    for md in md_files:
        content = _read_safe(md)
        rel = md.relative_to(root).as_posix()

        if len(content.encode()) < EMPTY_NOTE_BYTES:
            empty_notes.append(rel)
            continue

        # Check for YAML frontmatter
        if not content.startswith("---"):
            no_frontmatter.append(rel)

    issues: List[str] = []
    status = Status.PASS

    if empty_notes:
        issues.append(
            f"[warn] {len(empty_notes)} near-empty vault note(s) "
            f"(<{EMPTY_NOTE_BYTES} bytes):"
        )
        for n in empty_notes[:5]:
            issues.append(f"  - `{n}`")
        status = Status.WARN

    if no_frontmatter:
        issues.append(
            f"[warn] {len(no_frontmatter)} vault note(s) missing YAML frontmatter:"
        )
        for n in no_frontmatter[:5]:
            issues.append(f"  - `{n}`")
        status = worst([status, Status.WARN])

    if not issues:
        issues = [
            f"[ok] {len(md_files)} vault note(s) — frontmatter and size look healthy"
        ]

    return status, issues


def check_memory_density(root: Path) -> Tuple[Status, List[str]]:
    """WARN if total L1 key count exceeds DENSITY_LIMIT."""
    mem_dir = root / "memory"
    if not mem_dir.exists():
        return Status.PASS, ["[ok] No memory/ dir — density check skipped"]

    total = 0
    for jf in mem_dir.glob("*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                total += len(data)
            elif isinstance(data, list):
                total += len(data)
        except (json.JSONDecodeError, OSError):
            continue

    if total > DENSITY_LIMIT:
        return Status.WARN, [
            f"[warn] {total} total L1 memory entries exceed density limit "
            f"({DENSITY_LIMIT}) — consider pruning or archiving old entries"
        ]
    return Status.PASS, [
        f"[ok] {total} total L1 entries — within density limit ({DENSITY_LIMIT})"
    ]


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    checks = [
        ("## 1. L1 JSON Memory",    lambda: check_l1_memory(root)),
        ("## 2. Vault Notes",       lambda: check_vault_notes(root)),
        ("## 3. Memory Density",    lambda: check_memory_density(root)),
    ]

    section_texts: List[str] = []
    all_statuses:  List[Status] = []

    for heading, fn in checks:
        status, lines = fn()
        all_statuses.append(status)
        section_texts.append(f"{heading}  \n**Result:** {status.value}\n")
        for line in lines:
            section_texts.append(f"- {line}")
        section_texts.append("")

    overall = worst(all_statuses)
    if strict and overall == Status.WARN:
        overall = Status.FAIL

    verdict = (
        "Memory stores look clean."
        if overall == Status.PASS
        else "**Memory quality issues detected** — review above."
    )

    summary = (
        "## Summary\n\n"
        f"- Overall: **{overall.value}**\n"
        f"- {verdict}\n"
        + (f"- Mode: `--strict` (WARN -> FAIL)\n" if strict else "")
        + "\n"
    )

    write_report(REPORT_FILE, summary + "\n".join(section_texts), overall)
    return overall


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args:
        print(__doc__)
        return 0
    strict = "--strict" in args
    quiet  = "--quiet" in args
    status = run_check(strict=strict)
    if not quiet:
        icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]",
                "BLOCKED": "[BLOCKED]"}.get(status.value, "[?]")
        print(f"[memory_pollution_detector] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")
    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
