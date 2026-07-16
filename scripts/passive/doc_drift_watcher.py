"""
scripts/passive/doc_drift_watcher.py — SKILL 4

Passive doc-drift watcher.  Detects when public documentation no longer
reflects actual repository state.  NEVER auto-fixes anything.

Checks:
  1. Open ticket count     — WARN if any public doc claims differ from actual
  2. Closed ticket count   — WARN if any public doc claims differ from actual
  3. Solution count        — WARN if any public doc claims differ from actual
  4. Verify status         — WARN if any public doc claims differ from actual STATUS.md
  5. Archived doc refs     — WARN if public docs link to files in docs/_archive/

CLI:
  python scripts/passive/doc_drift_watcher.py --check
  python scripts/passive/doc_drift_watcher.py --strict
  python scripts/passive/doc_drift_watcher.py --quiet
  python scripts/passive/doc_drift_watcher.py --help
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

REPORT_FILE = "doc_drift_watcher.md"

# Public docs to scan for claims
PUBLIC_DOCS = ["README.md", "ABOUT.md", "PI.md", "vault/README.md"]

# PI.md auto-generated section markers
AUTO_BEGIN = "<!-- BEGIN AUTO §4 -->"
AUTO_END   = "<!-- END AUTO §4 -->"

# CHECKPOINTS
CHECKPOINTS_CURRENT = "CHECKPOINTS/current.md"


# ── Data extraction ───────────────────────────────────────────────────────────

def _read_safe(path: Path) -> Optional[str]:
    """Read a file, returning None on any error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _extract_auto_section(pi_md_text: str) -> Optional[str]:
    """Extract the §4 auto-generated block from PI.md, or None if absent."""
    m = re.search(
        re.escape(AUTO_BEGIN) + r"(.*?)" + re.escape(AUTO_END),
        pi_md_text,
        re.DOTALL,
    )
    return m.group(1).strip() if m else None


def _parse_auto_int(auto_text: str, key: str) -> Optional[int]:
    """Parse '- **Key:** N' from the §4 auto-block."""
    m = re.search(
        rf"\*\*{re.escape(key)}:\*\*\s*(\d+)",
        auto_text,
        re.IGNORECASE,
    )
    return int(m.group(1)) if m else None


def _parse_auto_str(auto_text: str, key: str) -> Optional[str]:
    """Parse '- **Key:** VALUE ...' (first word after colon) from the §4 auto-block."""
    m = re.search(
        rf"\*\*{re.escape(key)}:\*\*\s*(\S+)",
        auto_text,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


def _count_json_files(directory: Path) -> int:
    """Count *.json files in a directory; 0 if directory absent."""
    if not directory.exists():
        return 0
    return len(list(directory.glob("*.json")))


def _count_jsonl_lines(path: Path) -> int:
    """Count non-empty lines in a JSONL file; 0 if absent."""
    text = _read_safe(path)
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _parse_overall_status(status_md_text: str) -> Optional[str]:
    """Extract Overall: PASS/FAIL/WARN from STATUS.md."""
    m = re.search(
        r"\*\*Overall:\*\*\s*(\w+)",
        status_md_text,
    )
    return m.group(1).upper() if m else None


def _find_archived_refs(text: str, doc_name: str) -> List[str]:
    """Return list of _archive/ file references found in text (not bare directory mentions)."""
    # Match only paths that include an actual filename (word chars + dots after the prefix)
    hits = re.findall(r"docs/_archive/[\w.\-/]+", text)
    # Filter to paths that look like actual files (contain a dot) not bare dir refs
    files = [h for h in hits if "." in h.split("/")[-1]]
    return [f"`{doc_name}` references archived path: `{h}`" for h in files]


# ── Individual checks ─────────────────────────────────────────────────────────

def check_open_tickets(root: Path) -> Tuple[Status, List[str]]:
    """WARN if PI.md §4 open-ticket claim differs from actual count."""
    actual = _count_json_files(root / "tickets" / "open")

    pi_text = _read_safe(root / "PI.md")
    if pi_text is None:
        return Status.WARN, ["[warn] `PI.md` not found — cannot check open ticket count"]

    auto = _extract_auto_section(pi_text)
    if auto is None:
        return Status.WARN, [
            "[warn] PI.md §4 auto-section not found — has `refresh_pi.py` been run?"
        ]

    claimed = _parse_auto_int(auto, "Open tickets")
    if claimed is None:
        return Status.WARN, ["[warn] Could not parse 'Open tickets' from PI.md §4"]

    if claimed != actual:
        return Status.WARN, [
            f"[warn] Open tickets: PI.md claims {claimed}, actual {actual}  "
            f"*(run `python scripts/refresh_pi.py` to sync)*"
        ]
    return Status.PASS, [f"[ok] Open ticket count matches ({actual})"]


def check_closed_tickets(root: Path) -> Tuple[Status, List[str]]:
    """WARN if PI.md §4 closed-ticket claim differs from actual count."""
    actual = _count_json_files(root / "tickets" / "closed")

    pi_text = _read_safe(root / "PI.md")
    if pi_text is None:
        return Status.WARN, ["[warn] `PI.md` not found — cannot check closed ticket count"]

    auto = _extract_auto_section(pi_text)
    if auto is None:
        return Status.WARN, ["[warn] PI.md §4 auto-section not found"]

    claimed = _parse_auto_int(auto, "Closed tickets")
    if claimed is None:
        return Status.WARN, ["[warn] Could not parse 'Closed tickets' from PI.md §4"]

    if claimed != actual:
        return Status.WARN, [
            f"[warn] Closed tickets: PI.md claims {claimed}, actual {actual}  "
            f"*(run `python scripts/refresh_pi.py` to sync)*"
        ]
    return Status.PASS, [f"[ok] Closed ticket count matches ({actual})"]


def check_solution_count(root: Path) -> Tuple[Status, List[str]]:
    """WARN if PI.md §4 solutions claim differs from actual JSONL line count."""
    actual = _count_jsonl_lines(root / "solutions" / "SOLUTIONS.jsonl")

    pi_text = _read_safe(root / "PI.md")
    if pi_text is None:
        return Status.WARN, ["[warn] `PI.md` not found — cannot check solution count"]

    auto = _extract_auto_section(pi_text)
    if auto is None:
        return Status.WARN, ["[warn] PI.md §4 auto-section not found"]

    claimed = _parse_auto_int(auto, "Solutions logged")
    if claimed is None:
        return Status.WARN, ["[warn] Could not parse 'Solutions logged' from PI.md §4"]

    if claimed != actual:
        return Status.WARN, [
            f"[warn] Solutions: PI.md claims {claimed}, actual {actual}  "
            f"*(run `python scripts/refresh_pi.py` to sync)*"
        ]
    return Status.PASS, [f"[ok] Solution count matches ({actual})"]


def check_verify_status(root: Path) -> Tuple[Status, List[str]]:
    """WARN if PI.md §4 verify-status claim differs from actual STATUS.md."""
    status_text = _read_safe(root / "docs" / "STATUS.md")
    if status_text is None:
        return Status.WARN, [
            "[warn] `docs/STATUS.md` not found — run `python scripts/verify.py` first"
        ]

    actual = _parse_overall_status(status_text)
    if actual is None:
        return Status.WARN, ["[warn] Could not parse Overall status from docs/STATUS.md"]

    pi_text = _read_safe(root / "PI.md")
    if pi_text is None:
        return Status.WARN, ["[warn] `PI.md` not found — cannot check verify status claim"]

    auto = _extract_auto_section(pi_text)
    if auto is None:
        return Status.WARN, ["[warn] PI.md §4 auto-section not found"]

    # "Last verify: PASS · ..." — extract first word after "Last verify:"
    m = re.search(r"\*\*Last verify:\*\*\s*(PASS|FAIL|WARN)", auto, re.IGNORECASE)
    if m is None:
        return Status.WARN, ["[warn] Could not parse 'Last verify' from PI.md §4"]

    claimed = m.group(1).upper()
    if claimed != actual:
        return Status.WARN, [
            f"[warn] Verify status: PI.md claims {claimed}, actual {actual}  "
            f"*(run `python scripts/refresh_pi.py` to sync)*"
        ]
    return Status.PASS, [f"[ok] Verify status matches ({actual})"]


def check_archived_refs(root: Path) -> Tuple[Status, List[str]]:
    """WARN if any public doc references a file path inside docs/_archive/."""
    findings: List[str] = []

    for rel in PUBLIC_DOCS:
        path = root / rel
        text = _read_safe(path)
        if text is None:
            continue
        hits = _find_archived_refs(text, rel)
        findings.extend(hits)

    # Also check CHECKPOINTS/current.md
    ck = root / CHECKPOINTS_CURRENT
    ck_text = _read_safe(ck)
    if ck_text:
        findings.extend(_find_archived_refs(ck_text, CHECKPOINTS_CURRENT))

    if not findings:
        return Status.PASS, ["[ok] No references to archived docs found"]

    return Status.WARN, [
        f"[warn] {f}  *(archived files should not be linked from active docs)*"
        for f in findings
    ]


# ── T-153: capability-vs-open-ticket drift ────────────────────────────────────

# Words too generic to be a reliable capability↔ticket join key.
_CAP_STOPWORDS = {
    "with", "from", "mode", "modes", "full", "loop", "tools", "more", "into",
    "root", "state", "based", "using", "fallback", "chain", "across",
}


def _load_open_tickets(root: Path) -> List[Dict]:
    """Load open tickets (cp1252-tolerant). Returns list of dicts."""
    import json
    out: List[Dict] = []
    tdir = root / "tickets" / "open"
    if not tdir.exists():
        return out
    for p in tdir.glob("*.json"):
        try:
            raw = p.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp1252")
            out.append(json.loads(text))
        except Exception:
            continue
    return out


def check_vault_brief_freshness(root: Path) -> Tuple[Status, List[str]]:
    """T-285: WARN if vault/notes/per-ticket/ briefs lag tickets/closed/.

    The vault mirror only synced at session exit until T-285 added a daily
    scheduler job; before that it silently froze for months while
    tickets/closed/ kept growing — PI.md §2 tells every session to read
    these briefs, so a stale mirror is a real drift, not cosmetic.
    """
    closed_dir = root / "tickets" / "closed"
    briefs_dir = root / "vault" / "notes" / "per-ticket"

    if not closed_dir.exists():
        return Status.WARN, ["[warn] tickets/closed/ not found — cannot check vault freshness"]

    def _ticket_num(name: str) -> int:
        m = re.match(r"T-(\d+)", name)
        return int(m.group(1)) if m else -1

    closed_nums = [_ticket_num(p.stem) for p in closed_dir.glob("*.json")]
    closed_nums = [n for n in closed_nums if n >= 0]
    if not closed_nums:
        return Status.PASS, ["[ok] no closed tickets to check"]
    max_closed = max(closed_nums)

    if not briefs_dir.exists():
        return Status.WARN, [
            f"[warn] vault/notes/per-ticket/ missing entirely — highest closed ticket is T-{max_closed}, "
            f"0 briefs exist  *(sync_vault() has likely never run)*"
        ]

    brief_nums = [_ticket_num(p.stem) for p in briefs_dir.glob("T-*.md")]
    brief_nums = [n for n in brief_nums if n >= 0]
    max_brief = max(brief_nums) if brief_nums else -1

    gap = max_closed - max_brief
    if gap > 0:
        return Status.WARN, [
            f"[warn] vault briefs stop at T-{max_brief} but highest closed ticket is T-{max_closed} "
            f"({gap} ticket(s) behind)  *(run sync_vault or wait for the daily 03:45 job, T-285)*"
        ]
    return Status.PASS, [f"[ok] vault briefs current through T-{max_brief}"]


def check_capability_drift(root: Path) -> Tuple[Status, List[str]]:
    """WARN when ABOUT.md marks a capability 'Working' but an open P1/P2 ticket
    names it (T-153). Honest docs should track the ticket queue, not drift toward
    optimism. Heuristic join on capability keywords ≥5 chars in ticket text.
    """
    text = _read_safe(root / "ABOUT.md")
    if text is None:
        return Status.PASS, ["[ok] ABOUT.md not present — capability drift not checked"]

    rows = re.findall(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$", text, re.MULTILINE)
    working = [
        cap for cap, status, _ in rows
        if "working" in status.lower()
        and "partial" not in status.lower()
        and "known" not in status.lower()
        and cap.lower() not in ("capability",)  # skip header
    ]
    if not working:
        return Status.PASS, ["[ok] No 'Working' capability rows to cross-check"]

    open_p12 = [
        t for t in _load_open_tickets(root)
        if str(t.get("severity", "")).upper() in ("P1", "P2")
    ]
    if not open_p12:
        return Status.PASS, [f"[ok] {len(working)} 'Working' rows, no open P1/P2 to contradict"]

    findings: List[str] = []
    for cap in working:
        keywords = {w for w in re.findall(r"[a-z]{5,}", cap.lower())
                    if w not in _CAP_STOPWORDS}
        if not keywords:
            continue
        for t in open_p12:
            blob = f"{t.get('title','')} {t.get('component','')}".lower()
            hit = next((k for k in keywords if k in blob), None)
            if hit:
                findings.append(
                    f"[warn] '{cap}' marked Working, but open {t.get('severity')} "
                    f"{t.get('id')} mentions '{hit}'  *(verify the claim or mark Partial)*"
                )
                break

    if not findings:
        return Status.PASS, ["[ok] No 'Working' capability contradicted by an open P1/P2 ticket"]
    return Status.WARN, findings


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    """Run all doc-drift checks; write report; return overall Status."""
    checks = [
        ("## 1. Open Ticket Count",    lambda: check_open_tickets(root)),
        ("## 2. Closed Ticket Count",  lambda: check_closed_tickets(root)),
        ("## 3. Solution Count",       lambda: check_solution_count(root)),
        ("## 4. Verify Status",        lambda: check_verify_status(root)),
        ("## 5. Archived Doc Refs",    lambda: check_archived_refs(root)),
        ("## 6. Capability vs Open Tickets", lambda: check_capability_drift(root)),
        ("## 7. Vault Brief Freshness", lambda: check_vault_brief_freshness(root)),
    ]

    section_texts: List[str] = []
    all_statuses: List[Status] = []

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
        "Public docs are in sync with repo state."
        if overall == Status.PASS
        else "**Doc drift detected** — run `python scripts/refresh_pi.py` to resync."
        if overall in (Status.WARN, Status.FAIL)
        else "Could not fully check doc drift — see details above."
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


# ── CLI ───────────────────────────────────────────────────────────────────────

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
        print(f"[doc_drift_watcher] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")

    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
