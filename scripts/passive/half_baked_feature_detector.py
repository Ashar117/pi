"""
scripts/passive/half_baked_feature_detector.py — SKILL 6

Passive half-baked feature detector.  Scans code for features that exist
but aren't fully implemented.  NEVER auto-fixes anything.

Checks:
  1. Stub implementations   — WARN: raise NotImplementedError / bare pass bodies
  2. Tools without tests    — WARN: tools/tools_X.py with no testing/test_tools_X.py
  3. TODO / FIXME markers   — WARN: unresolved markers in Python source
  4. Graceful import traps  — WARN: try/except ImportError that silences a dep
  5. Unused .env vars       — WARN: keys in .env.example not referenced in code
  6. Orphaned tool files    — WARN: tools/tools_X.py not imported in agent/tools.py

CLI:
  python scripts/passive/half_baked_feature_detector.py --check
  python scripts/passive/half_baked_feature_detector.py --strict
  python scripts/passive/half_baked_feature_detector.py --quiet
  python scripts/passive/half_baked_feature_detector.py --help
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import List, Set, Tuple

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

REPORT_FILE = "half_baked_features.md"

# Directories to scan for stubs / markers / imports
SCAN_DIRS = ["tools", "agent", "scripts", "core", "memory", "llm", "app"]
# Directories excluded from scans (passive scripts themselves are allowed to be simple)
EXCLUDE_PREFIXES = ("scripts/passive",)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _py_files(root: Path) -> List[Path]:
    """All .py files under the scan dirs, skipping excluded prefixes."""
    files: List[Path] = []
    for d in SCAN_DIRS:
        scan = root / d
        if not scan.exists():
            continue
        for p in sorted(scan.rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            if not any(rel.startswith(ex) for ex in EXCLUDE_PREFIXES):
                files.append(p)
    return files


def _is_stub_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function body is just 'pass', a docstring+pass, or raises NotImplementedError."""
    body = node.body
    # Strip docstring
    real = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if not real:
        return True  # only docstring or empty
    if len(real) == 1:
        s = real[0]
        # bare pass
        if isinstance(s, ast.Pass):
            return True
        # raise NotImplementedError(...)
        if isinstance(s, ast.Raise) and s.exc is not None:
            exc = s.exc
            name = None
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                name = exc.func.id
            elif isinstance(exc, ast.Name):
                name = exc.id
            if name == "NotImplementedError":
                return True
    return False


# ── Individual checks ─────────────────────────────────────────────────────────

def check_stub_implementations(root: Path) -> Tuple[Status, List[str]]:
    """WARN for each function that is a stub (pass / NotImplementedError)."""
    stubs: List[str] = []
    for path in _py_files(root):
        source = _read_safe(path)
        if not source:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_") and _is_stub_function(node):
                    stubs.append(f"`{rel}:{node.lineno}` — `{node.name}()` is a stub")

    if not stubs:
        return Status.PASS, ["[ok] No stub implementations found"]
    lines = [f"[warn] {len(stubs)} stub function(s) detected:"] + stubs
    return Status.WARN, lines


def check_tools_without_tests(root: Path) -> Tuple[Status, List[str]]:
    """WARN for each tools/tools_X.py with no test file AND no reference in any testing/*.py."""
    tools_dir   = root / "tools"
    testing_dir = root / "testing"
    if not tools_dir.exists():
        return Status.PASS, ["[skip] tools/ directory not found"]

    # Build a corpus of all test file content for grep-based coverage detection
    test_corpus = ""
    if testing_dir.exists():
        for tf in sorted(testing_dir.glob("*.py")):
            test_corpus += _read_safe(tf)

    missing: List[str] = []
    for tool_file in sorted(tools_dir.glob("tools_*.py")):
        stem      = tool_file.stem          # e.g. tools_memory
        test_file = testing_dir / f"test_{stem}.py"
        # Pass if dedicated test file exists OR stem is referenced in any test file
        if not test_file.exists() and stem not in test_corpus:
            missing.append(f"`tools/{tool_file.name}` — no `testing/test_{stem}.py`")

    if not missing:
        return Status.PASS, ["[ok] All tool files have corresponding test files"]
    lines = [f"[warn] {len(missing)} tool file(s) with no test file:"] + missing
    return Status.WARN, lines


def check_todo_markers(root: Path) -> Tuple[Status, List[str]]:
    """WARN for each TODO / FIXME / STUB / HACK marker in Python source."""
    pattern = re.compile(r"#.*\b(TODO|FIXME|STUB|HACK)\b", re.IGNORECASE)
    found: List[str] = []
    for path in _py_files(root):
        source = _read_safe(path)
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(source.splitlines(), 1):
            m = pattern.search(line)
            if m:
                snippet = line.strip()[:80]
                found.append(f"`{rel}:{i}` — {snippet}")

    if not found:
        return Status.PASS, ["[ok] No TODO/FIXME/STUB/HACK markers found"]
    lines = [f"[warn] {len(found)} unresolved marker(s):"] + found
    return Status.WARN, lines


def check_graceful_import_traps(root: Path) -> Tuple[Status, List[str]]:
    """WARN for try/except ImportError blocks that silence a missing dependency."""
    # Intentionally optional deps — documented fallbacks, should not WARN.
    _KNOWN_OPTIONAL: Set[str] = {
        # voice / audio  (tools_wakeword, voice_loop)
        "wake_det", "WakeWordDetector",
        # BM25 hybrid retrieval (tools_memory) — falls back to LIKE search
        "_BM25Okapi",
        # NetworkX KG (knowledge_graph) — falls back to SQL BFS
        "nx",
        # silero-vad (voice_loop)
        "vad_model",
    }

    # Match: except ImportError: where the very next non-empty line assigns X = None
    pattern = re.compile(
        r"except\s+ImportError[^:]*:\s*\n[ \t]+(\w+)\s*=\s*None",
        re.MULTILINE,
    )
    found: List[str] = []
    for path in _py_files(root):
        source = _read_safe(path)
        rel = path.relative_to(root).as_posix()
        for m in pattern.finditer(source):
            dep = m.group(1)
            if dep in _KNOWN_OPTIONAL:
                continue
            lineno = source[: m.start()].count("\n") + 1
            found.append(
                f"`{rel}:{lineno}` — `{dep}` silenced on ImportError "
                f"*(optional dep — may cause silent failures)*"
            )

    if not found:
        return Status.PASS, ["[ok] No silenced optional dependencies found"]
    lines = [f"[warn] {len(found)} silenced import(s) detected:"] + found
    return Status.WARN, lines


def check_unused_env_vars(root: Path) -> Tuple[Status, List[str]]:
    """WARN for keys in .env.example that are never referenced in any Python file."""
    env_example = root / ".env.example"
    if not env_example.exists():
        return Status.PASS, ["[skip] `.env.example` not found"]

    # Parse KEY=... lines
    keys: Set[str] = set()
    for line in _read_safe(env_example).splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key:
                keys.add(key)

    if not keys:
        return Status.PASS, ["[ok] No keys defined in `.env.example`"]

    # Collect all Python source in repo
    all_source = ""
    for path in _py_files(root):
        all_source += _read_safe(path)

    unused = sorted(k for k in keys if k not in all_source)
    if not unused:
        return Status.PASS, [f"[ok] All {len(keys)} `.env.example` key(s) referenced in code"]
    lines = [
        f"[warn] {len(unused)} `.env.example` key(s) not referenced in any Python file:"
    ] + [f"  `{k}`" for k in unused]
    return Status.WARN, lines


def check_orphaned_tool_files(root: Path) -> Tuple[Status, List[str]]:
    """WARN for tools/tools_X.py files not imported in agent/tools.py, pi_agent.py, or voice_loop.py."""
    tools_dir = root / "tools"
    if not tools_dir.exists():
        return Status.PASS, ["[skip] tools/ directory not found"]

    # Scan all agent-adjacent files that legitimately import tool modules
    import_sources = [
        root / "agent" / "tools.py",
        root / "pi_agent.py",
        root / "agent" / "voice_loop.py",
    ]
    combined_source = "".join(_read_safe(p) for p in import_sources)
    if not combined_source.strip():
        return Status.PASS, ["[skip] No tool import files found"]

    orphaned: List[str] = []
    for tool_file in sorted(tools_dir.glob("tools_*.py")):
        stem = tool_file.stem  # e.g. tools_memory
        if stem not in combined_source and tool_file.name not in combined_source:
            orphaned.append(
                f"`tools/{tool_file.name}` — not imported in agent/tools.py, pi_agent.py, or voice_loop.py"
            )

    if not orphaned:
        return Status.PASS, ["[ok] All tool files are imported in a known agent file"]
    lines = [f"[warn] {len(orphaned)} orphaned tool file(s):"] + orphaned
    return Status.WARN, lines


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    """Run all half-baked feature checks; write report; return overall Status."""
    checks = [
        ("## 1. Stub Implementations",
         lambda: check_stub_implementations(root)),
        ("## 2. Tools Without Tests",
         lambda: check_tools_without_tests(root)),
        ("## 3. TODO / FIXME Markers",
         lambda: check_todo_markers(root)),
        ("## 4. Graceful Import Traps",
         lambda: check_graceful_import_traps(root)),
        ("## 5. Unused .env.example Vars",
         lambda: check_unused_env_vars(root)),
        ("## 6. Orphaned Tool Files",
         lambda: check_orphaned_tool_files(root)),
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

    # LLM triage — separate "actively-WIP" from "truly-abandoned" stubs
    if overall != Status.PASS and all_raw_lines:
        try:
            from agent.skill_triage import triage
            triage_md = triage(
                skill_name="half_baked_feature_detector",
                findings_summary=f"Overall {overall.value}; {len(all_raw_lines)} half-baked signals across 6 checks",
                raw_lines=all_raw_lines,
                question="Which of these are actively under development vs. truly abandoned? Prefer flagging orphaned tools and stubs over TODO markers.",
            )
            if triage_md:
                section_texts.append(triage_md)
        except Exception:
            pass

    verdict = (
        "No half-baked features detected."
        if overall == Status.PASS
        else "**Half-baked features detected** — review items above."
        if overall in (Status.WARN, Status.FAIL)
        else "Could not fully check — see details above."
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
        print(f"[half_baked_feature_detector] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")

    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
