"""
scripts/passive/tech_debt_accumulator.py — SKILL 11

Passive tech-debt accumulator.  Counts and categorises debt signals across
the codebase: TODO/FIXME/HACK comments, pytest.mark.skip usages,
swallowed exceptions (bare except/pass), and type: ignore suppressions.
NEVER modifies source files.

Checks:
  1. TODO/FIXME density  — WARN if total > HIGH_DENSITY threshold
  2. Skipped tests       — WARN if skip count > SKIP_THRESHOLD
  3. Swallowed exceptions— WARN if bare except/pass patterns > EXC_THRESHOLD
  4. Type-ignore count   — WARN if `# type: ignore` count > TYPE_THRESHOLD

Output: reports/tech_debt_accumulator.md

CLI:
  python scripts/passive/tech_debt_accumulator.py --check
  python scripts/passive/tech_debt_accumulator.py --strict
  python scripts/passive/tech_debt_accumulator.py --quiet
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

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

REPORT_FILE     = "tech_debt_accumulator.md"

HIGH_DENSITY    = 50    # total TODO/FIXME/HACK across all scanned files
SKIP_THRESHOLD  = 10    # number of pytest skips
EXC_THRESHOLD   = 15    # swallowed exception patterns
TYPE_THRESHOLD  = 20    # type: ignore comments

SCAN_DIRS = ["tools", "agent", "scripts", "core", "memory", "llm", "app", "testing"]
EXCLUDE_PREFIXES = ("scripts/passive",)   # don't flag our own skill stubs


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _py_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for d in SCAN_DIRS:
        src_dir = root / d
        if not src_dir.exists():
            continue
        for py in sorted(src_dir.rglob("*.py")):
            rel = py.relative_to(root).as_posix()
            if any(rel.startswith(ex) for ex in EXCLUDE_PREFIXES):
                continue
            files.append(py)
    return files


# ── Individual checks ─────────────────────────────────────────────────────────

def check_todo_density(root: Path) -> Tuple[Status, List[str]]:
    """WARN if TODO/FIXME/HACK density exceeds HIGH_DENSITY."""
    pattern = re.compile(r"#\s*(TODO|FIXME|HACK|STUB)\b", re.IGNORECASE)
    files = _py_files(root)
    if not files:
        return Status.PASS, ["[ok] No Python source dirs found — skipped"]

    total = 0
    by_file: Dict[str, int] = {}
    for py in files:
        src = _read_safe(py)
        count = len(pattern.findall(src))
        if count:
            rel = py.relative_to(root).as_posix()
            by_file[rel] = count
            total += count

    if total > HIGH_DENSITY:
        lines = [
            f"[warn] {total} TODO/FIXME/HACK markers across {len(by_file)} file(s) "
            f"(threshold: {HIGH_DENSITY}):"
        ]
        for f, n in sorted(by_file.items(), key=lambda x: -x[1])[:8]:
            lines.append(f"  - `{f}` — {n}×")
        return Status.WARN, lines

    return Status.PASS, [
        f"[ok] {total} TODO/FIXME/HACK markers — within threshold ({HIGH_DENSITY})"
    ]


def check_skipped_tests(root: Path) -> Tuple[Status, List[str]]:
    """WARN if too many tests are skipped."""
    pattern = re.compile(r"@pytest\.mark\.skip|pytest\.skip\(", re.IGNORECASE)
    test_dir = root / "testing"
    if not test_dir.exists():
        return Status.PASS, ["[ok] No testing/ dir — skipped test check skipped"]

    total = 0
    by_file: Dict[str, int] = {}
    for py in sorted(test_dir.rglob("*.py")):
        src = _read_safe(py)
        count = len(pattern.findall(src))
        if count:
            rel = py.relative_to(root).as_posix()
            by_file[rel] = count
            total += count

    if total > SKIP_THRESHOLD:
        lines = [
            f"[warn] {total} skipped test(s) across {len(by_file)} file(s) "
            f"(threshold: {SKIP_THRESHOLD}):"
        ]
        for f, n in sorted(by_file.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  - `{f}` — {n} skip(s)")
        return Status.WARN, lines

    return Status.PASS, [
        f"[ok] {total} skipped test(s) — within threshold ({SKIP_THRESHOLD})"
    ]


def check_swallowed_exceptions(root: Path) -> Tuple[Status, List[str]]:
    """WARN if too many bare except/pass patterns suppress errors silently."""
    bare_except = re.compile(r"^\s*except\s*(?:Exception\s*)?:\s*$", re.MULTILINE)
    pass_in_except = re.compile(
        r"except[^:]*:\s*\n\s*pass\b", re.MULTILINE
    )
    files = _py_files(root)
    if not files:
        return Status.PASS, ["[ok] No source dirs found — skipped"]

    total = 0
    by_file: Dict[str, int] = {}
    for py in files:
        src = _read_safe(py)
        count = len(bare_except.findall(src)) + len(pass_in_except.findall(src))
        if count:
            rel = py.relative_to(root).as_posix()
            by_file[rel] = count
            total += count

    if total > EXC_THRESHOLD:
        lines = [
            f"[warn] {total} swallowed-exception pattern(s) in {len(by_file)} file(s) "
            f"(threshold: {EXC_THRESHOLD}):"
        ]
        for f, n in sorted(by_file.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  - `{f}` — {n}×")
        return Status.WARN, lines

    return Status.PASS, [
        f"[ok] {total} swallowed-exception pattern(s) — within threshold ({EXC_THRESHOLD})"
    ]


def check_type_ignores(root: Path) -> Tuple[Status, List[str]]:
    """WARN if too many `# type: ignore` suppressions."""
    pattern = re.compile(r"#\s*type:\s*ignore", re.IGNORECASE)
    files = _py_files(root)
    if not files:
        return Status.PASS, ["[ok] No source dirs found — skipped"]

    total = 0
    by_file: Dict[str, int] = {}
    for py in files:
        src = _read_safe(py)
        count = len(pattern.findall(src))
        if count:
            rel = py.relative_to(root).as_posix()
            by_file[rel] = count
            total += count

    if total > TYPE_THRESHOLD:
        lines = [
            f"[warn] {total} `# type: ignore` suppression(s) in {len(by_file)} file(s) "
            f"(threshold: {TYPE_THRESHOLD}):"
        ]
        for f, n in sorted(by_file.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  - `{f}` — {n}×")
        return Status.WARN, lines

    return Status.PASS, [
        f"[ok] {total} type-ignore suppression(s) — within threshold ({TYPE_THRESHOLD})"
    ]


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    checks = [
        ("## 1. TODO/FIXME Density",      lambda: check_todo_density(root)),
        ("## 2. Skipped Tests",            lambda: check_skipped_tests(root)),
        ("## 3. Swallowed Exceptions",     lambda: check_swallowed_exceptions(root)),
        ("## 4. Type-Ignore Suppressions", lambda: check_type_ignores(root)),
    ]

    section_texts: List[str] = []
    all_statuses:  List[Status] = []

    all_raw_lines: List[str] = []
    for heading, fn in checks:
        status, lines = fn()
        all_statuses.append(status)
        section_texts.append(f"{heading}  \n**Result:** {status.value}\n")
        for line in lines:
            section_texts.append(f"- {line}")
            all_raw_lines.append(f"- {line}")
        section_texts.append("")

    overall = worst(all_statuses)
    if strict and overall == Status.WARN:
        overall = Status.FAIL

    # LLM triage (Groq) — adds prioritisation when overall != PASS
    if overall != Status.PASS and all_raw_lines:
        try:
            from agent.skill_triage import triage
            triage_md = triage(
                skill_name="tech_debt_accumulator",
                findings_summary=f"Overall status {overall.value}; {len(all_raw_lines)} debt signals across 4 categories",
                raw_lines=all_raw_lines,
                question="Which debt items are critical-path blockers vs. cosmetic? Prioritise by impact on Pi's autonomy.",
            )
            if triage_md:
                section_texts.append(triage_md)
        except Exception:
            pass  # triage is best-effort; never break the skill

    verdict = (
        "Tech-debt signals are within acceptable thresholds."
        if overall == Status.PASS
        else "**Tech-debt accumulation detected** — review above."
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
        print(f"[tech_debt_accumulator] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")
    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
