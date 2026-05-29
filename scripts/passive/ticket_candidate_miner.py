"""
scripts/passive/ticket_candidate_miner.py — SKILL 8

Passive ticket candidate miner.  Scans logs, reports, and code comments for
problems worth ticketing.  Deduplicates against existing tickets.
NEVER creates tickets automatically.

Sources:
  1. docs/STATUS.md           — parse failure messages
  2. CHECKPOINTS/current.md   — scan for blockers / TODOs
  3. reports/*.md              — collect FAIL / WARN statuses
  4. Code TODOs / FIXMEs       — grep Python source
  5. Repeated patterns in logs/turns.jsonl (last N turns)

Output: analysis/candidate_tickets.jsonl  (append-only)

CLI:
  python scripts/passive/ticket_candidate_miner.py --check
  python scripts/passive/ticket_candidate_miner.py --strict
  python scripts/passive/ticket_candidate_miner.py --quiet
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    REPORTS as _DEFAULT_REPORTS,
    TICKETS_OPEN,
    TICKETS_CLOSED,
    Status,
    read_jsonl,
    append_jsonl,
    write_report,
    status_to_exit_code,
    worst,
)

REPORT_FILE      = "ticket_candidate_miner.md"
CANDIDATES_FILE  = "analysis/candidate_tickets.jsonl"
MAX_LOG_TURNS    = 200   # scan last N turns.jsonl entries

# Source tags for candidates
SRC_STATUS   = "status_md"
SRC_CHECKS   = "checkpoints"
SRC_REPORTS  = "passive_reports"
SRC_CODE     = "code_marker"
SRC_LOGS     = "turn_logs"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _candidate_id(source: str, description: str) -> str:
    """Stable short ID for dedup — hash of source+description."""
    raw = f"{source}:{description[:120]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _load_existing_titles(open_dir: Path, closed_dir: Path) -> Set[str]:
    """Collect lowercased titles of all existing open/closed tickets."""
    titles: Set[str] = set()
    for directory in [open_dir, closed_dir]:
        if not directory.exists():
            continue
        for p in directory.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                t = (data.get("title") or "").lower().strip()
                if t:
                    titles.add(t)
            except (json.JSONDecodeError, OSError):
                continue
    return titles


def _is_duplicate(title: str, existing_titles: Set[str]) -> bool:
    """True if a similar title already exists in open/closed tickets."""
    tl = title.lower().strip()
    # Exact match
    if tl in existing_titles:
        return True
    # First 40-char prefix overlap
    prefix = tl[:40]
    return any(prefix in et for et in existing_titles)


def _make_candidate(source: str, title: str, description: str, severity: str = "P2") -> Dict:
    return {
        "id":          _candidate_id(source, title),
        "source":      source,
        "title":       title[:80],
        "description": description[:300],
        "severity":    severity,
        "found_at":    datetime.now(timezone.utc).isoformat(),
    }


# ── Scanners ──────────────────────────────────────────────────────────────────

def scan_status_md(root: Path) -> List[Dict]:
    text = _read_safe(root / "docs" / "STATUS.md")
    if not text:
        return []
    candidates: List[Dict] = []
    # Collect FAILED test lines
    for line in text.splitlines():
        m = re.search(r"FAILED\s+([\w/.\-:]+)", line)
        if m:
            test_id = m.group(1)
            candidates.append(_make_candidate(
                SRC_STATUS,
                f"Fix failing test: {test_id}",
                f"Test `{test_id}` is failing according to docs/STATUS.md",
                "P1",
            ))
    return candidates


def scan_checkpoints(root: Path) -> List[Dict]:
    text = _read_safe(root / "CHECKPOINTS" / "current.md")
    if not text:
        return []
    candidates: List[Dict] = []
    # Lines with explicit TODO / BLOCKER / BUG
    markers = re.compile(r"\b(TODO|BLOCKER|BUG|BLOCKED|FIXME)\b", re.IGNORECASE)
    for line in text.splitlines():
        if markers.search(line):
            snippet = line.strip()[:80]
            candidates.append(_make_candidate(
                SRC_CHECKS,
                f"Checkpoint item: {snippet}",
                f"Checkpoint contains marker: {line.strip()[:200]}",
                "P2",
            ))
    return candidates


def scan_passive_reports(reports_dir: Path) -> List[Dict]:
    """Collect FAIL statuses from all passive skill reports."""
    if not reports_dir.exists():
        return []
    candidates: List[Dict] = []
    for report in sorted(reports_dir.glob("*.md")):
        text = _read_safe(report)
        m = re.search(r"\*\*Status:\*\*\s*(FAIL|WARN)", text)
        if m and m.group(1) == "FAIL":
            candidates.append(_make_candidate(
                SRC_REPORTS,
                f"Fix passive skill FAIL: {report.stem}",
                f"`reports/{report.name}` reports FAIL status.",
                "P2",
            ))
    return candidates


def scan_code_markers(root: Path) -> List[Dict]:
    """Collect TODO/FIXME comments from Python source across private dirs."""
    pattern = re.compile(r"#\s*(TODO|FIXME|HACK|STUB)\s*[:\-]?\s*(.+)", re.IGNORECASE)
    scan_dirs = ["tools", "agent", "scripts", "core", "memory", "llm", "app"]
    candidates: List[Dict] = []
    seen: Set[str] = set()
    for d in scan_dirs:
        src_dir = root / d
        if not src_dir.exists():
            continue
        for py in sorted(src_dir.rglob("*.py")):
            rel = py.relative_to(root).as_posix()
            if rel.startswith("scripts/passive"):
                continue
            source = _read_safe(py)
            for i, line in enumerate(source.splitlines(), 1):
                m = pattern.search(line)
                if m:
                    marker  = m.group(1).upper()
                    comment = m.group(2).strip()[:60]
                    title   = f"{marker} in {rel}:{i}: {comment}"
                    key     = _candidate_id(SRC_CODE, title)
                    if key not in seen:
                        seen.add(key)
                        candidates.append(_make_candidate(
                            SRC_CODE, title,
                            f"`{rel}` line {i}: `{line.strip()[:200]}`",
                            "P3",
                        ))
    return candidates


def scan_turn_logs(root: Path, max_turns: int = MAX_LOG_TURNS) -> List[Dict]:
    """Detect repeated error patterns in recent turns.jsonl."""
    logs_path = root / "logs" / "turns.jsonl"
    if not logs_path.exists():
        return []
    try:
        turns = read_jsonl(logs_path)
    except Exception:
        return []

    recent = turns[-max_turns:]
    error_counts: Dict[str, int] = {}
    for t in recent:
        content = str(t.get("content") or t.get("response") or "")
        for pat in [r"(Error|Exception|Traceback).*", r"FAILED [\w/.:]+", r"ImportError.*"]:
            for m in re.finditer(pat, content):
                key = m.group(0)[:60]
                error_counts[key] = error_counts.get(key, 0) + 1

    candidates: List[Dict] = []
    for msg, count in error_counts.items():
        if count >= 3:
            candidates.append(_make_candidate(
                SRC_LOGS,
                f"Recurring error ({count}x): {msg[:50]}",
                f"Seen {count} times in last {len(recent)} turns: `{msg}`",
                "P2",
            ))
    return candidates


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    existing_titles = _load_existing_titles(
        root / "tickets" / "open",
        root / "tickets" / "closed",
    )

    all_candidates: List[Dict] = []
    for scan_fn, args in [
        (scan_status_md,       (root,)),
        (scan_checkpoints,     (root,)),
        (scan_passive_reports, (reports,)),
        (scan_code_markers,    (root,)),
        (scan_turn_logs,       (root,)),
    ]:
        try:
            all_candidates.extend(scan_fn(*args))
        except Exception:
            pass

    # Deduplicate
    new_candidates = [c for c in all_candidates if not _is_duplicate(c["title"], existing_titles)]

    # Append new ones to JSONL
    out_path = root / CANDIDATES_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for c in new_candidates:
        append_jsonl(out_path, c)

    # Build report
    section_lines: List[str] = []
    if not new_candidates:
        section_lines = ["- [ok] No new ticket candidates found"]
        overall = Status.PASS
    else:
        p0p1 = [c for c in new_candidates if c["severity"] in ("P0", "P1")]
        overall = Status.WARN
        section_lines.append(
            f"- [warn] {len(new_candidates)} new candidate(s) written to "
            f"`{CANDIDATES_FILE}`"
        )
        for c in new_candidates[:20]:  # show up to 20
            section_lines.append(
                f"  - [{c['severity']}] `{c['source']}` — {c['title']}"
            )
        if p0p1:
            overall = Status.FAIL

    if strict and overall == Status.WARN:
        overall = Status.FAIL

    verdict = (
        "No new ticket candidates."
        if overall == Status.PASS
        else f"**{len(new_candidates)} new candidate(s) found** — review `{CANDIDATES_FILE}`."
    )
    summary = (
        "## Summary\n\n"
        f"- Overall: **{overall.value}**\n"
        f"- {verdict}\n"
        + (f"- Mode: `--strict` (WARN -> FAIL)\n" if strict else "")
        + "\n## Candidates\n\n"
    )

    body = summary + "\n".join(section_lines)

    # LLM triage — cluster similar candidates into consolidated tickets
    if new_candidates:
        try:
            from agent.skill_triage import triage
            raw = [f"[{c['severity']}] {c['source']} — {c['title']}" for c in new_candidates[:40]]
            triage_md = triage(
                skill_name="ticket_candidate_miner",
                findings_summary=f"{len(new_candidates)} new ticket candidates ({len([c for c in new_candidates if c['severity'] in ('P0','P1')])} P0/P1)",
                raw_lines=raw,
                question="Cluster similar candidates into consolidated tickets. Flag any that look like duplicates of each other. Prioritise P0/P1.",
            )
            if triage_md:
                body += "\n\n" + triage_md
        except Exception:
            pass

    write_report(REPORT_FILE, body, overall)
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
        print(f"[ticket_candidate_miner] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")
    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
