"""
scripts/passive/consciousness_capability_sync.py — SKILL 5

Passive consciousness-capability sync checker.  Detects gaps between the
tools that exist in code and the tools Pi knows to use (mentioned in
prompts/consciousness.txt).  NEVER auto-fixes anything.

Checks:
  1. Missing tools  — WARN: tools in code but absent from consciousness.txt
  2. Phantom tools  — WARN: tool-like names in consciousness.txt with no code match
  3. Coverage ratio — PASS/WARN: percentage of code tools mentioned

CLI:
  python scripts/passive/consciousness_capability_sync.py --check
  python scripts/passive/consciousness_capability_sync.py --strict
  python scripts/passive/consciousness_capability_sync.py --quiet
  python scripts/passive/consciousness_capability_sync.py --help
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

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

REPORT_FILE = "consciousness_capability_sync.md"

CONSCIOUSNESS_PATH = "prompts/consciousness.txt"
TRIGGERS_PATH      = "prompts/triggers.md"

# Verb prefixes that identify a token as a likely tool name (vs. a parameter)
_TOOL_VERB_PREFIXES = (
    "read_", "write_", "search_", "create_", "delete_", "get_", "send_",
    "analyze_", "detect_", "list_", "run_", "save_", "modify_", "execute_",
    "transcribe_", "recognize_", "register_", "browse_", "refresh_",
    "obsidian_", "calendar_", "gmail_", "telegram_", "memory_", "web_",
    "reddit_", "scholar_", "ocr_", "system_", "speak_", "listen_",
)

# Suffixes that identify a token as a parameter name, not a tool name
_PARAM_SUFFIXES = (
    "_id", "_str", "_chars", "_frames", "_results", "_comments",
    "_confirmed", "_unconfirmed", "_path", "_dir", "_url", "_key",
    "_type", "_size", "_limit", "_count", "_name", "_text",
)

# Coverage threshold below which we WARN (fraction of code tools mentioned)
COVERAGE_WARN_THRESHOLD = 0.80


# ── Tool discovery ────────────────────────────────────────────────────────────

def _load_code_tools(root: Path) -> Tuple[Optional[Set[str]], Optional[str]]:
    """
    Try to import get_tool_definitions() from agent.tools.
    Returns (set_of_tool_names, None) on success, or (None, error_message) on failure.
    """
    try:
        import importlib
        spec = importlib.util.spec_from_file_location(
            "agent.tools", root / "agent" / "tools.py"
        )
        if spec is None or spec.loader is None:
            return None, "agent/tools.py not found"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        defs = mod.get_tool_definitions()
        names = {d["name"] for d in defs if isinstance(d, dict) and "name" in d}
        return names, None
    except Exception as exc:
        return None, str(exc)


def _read_safe(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _resolve_includes(text: str, prompts_dir: Path) -> str:
    """Expand {{INCLUDE:filename}} directives so the checker sees the full resolved text."""
    def _sub(m: re.Match) -> str:
        fname = m.group(1).strip()
        included = _read_safe(prompts_dir / fname)
        return included if included is not None else ""
    return re.sub(r"\{\{INCLUDE:([^}]+)\}\}", _sub, text)


def _load_consciousness_text(consciousness_path: Path) -> Optional[str]:
    """Read consciousness.txt and resolve all {{INCLUDE:}} directives."""
    raw = _read_safe(consciousness_path)
    if raw is None:
        return None
    prompts_dir = consciousness_path.parent
    return _resolve_includes(raw, prompts_dir)


def _extract_mentioned_tools(text: str, code_tools: Set[str]) -> Set[str]:
    """Return the subset of code_tools whose names appear in text."""
    return {t for t in code_tools if t in text}


def _extract_phantom_tools(text: str, code_tools: Set[str]) -> Set[str]:
    """
    Find snake_case identifiers in text that look like tool names but are NOT
    in code_tools.  Only flags identifiers with a recognised tool verb prefix
    and no param suffix.  Identifiers that appear only inside parentheses
    (i.e. as function arguments) are excluded since they are parameter names.
    """
    # Strip argument lists so parameter names like save_to_obsidian aren't flagged
    stripped = re.sub(r"\([^)]*\)", "()", text)
    candidates = set(re.findall(r"\b([a-z][a-z0-9_]{3,})\b", stripped))
    phantoms: Set[str] = set()
    for c in candidates:
        if c in code_tools:
            continue
        if not any(c.startswith(pfx) for pfx in _TOOL_VERB_PREFIXES):
            continue
        if any(c.endswith(sfx) for sfx in _PARAM_SUFFIXES):
            continue
        # Must contain at least one underscore (not a bare verb like "listen")
        if "_" not in c:
            continue
        phantoms.add(c)
    return phantoms


# ── Individual checks ─────────────────────────────────────────────────────────

def check_missing_tools(
    code_tools: Set[str],
    consciousness_path: Path,
    triggers_path: Optional[Path] = None,
) -> Tuple[Status, List[str]]:
    """WARN if any code tools are absent from consciousness.txt (and triggers.md)."""
    cons_text = _load_consciousness_text(consciousness_path)
    if cons_text is None:
        return Status.WARN, [
            f"[warn] `{consciousness_path.name}` not found — cannot check tool coverage"
        ]

    # Merge consciousness (with includes resolved) + triggers text for mention search
    full_text = cons_text
    if triggers_path is not None:
        tr_text = _read_safe(triggers_path)
        if tr_text:
            full_text += "\n" + tr_text

    mentioned = _extract_mentioned_tools(full_text, code_tools)
    missing   = sorted(code_tools - mentioned)

    if not missing:
        return Status.PASS, [
            f"[ok] All {len(code_tools)} code tools are mentioned in consciousness/triggers"
        ]

    lines = [
        f"[warn] {len(missing)} tool(s) in code but absent from consciousness/triggers:"
    ]
    for name in missing:
        lines.append(f"  - `{name}`")
    lines.append(
        "  *(Add these to `prompts/consciousness.txt` so Pi knows to use them)*"
    )
    return Status.WARN, lines


def check_phantom_tools(
    code_tools: Set[str],
    consciousness_path: Path,
) -> Tuple[Status, List[str]]:
    """WARN if consciousness.txt mentions tool-like names that don't exist in code."""
    cons_text = _load_consciousness_text(consciousness_path)
    if cons_text is None:
        return Status.PASS, ["[skip] consciousness.txt not found — skipping phantom check"]

    phantoms = sorted(_extract_phantom_tools(cons_text, code_tools))
    if not phantoms:
        return Status.PASS, ["[ok] No phantom tool references in consciousness.txt"]

    lines = [
        f"[warn] {len(phantoms)} tool-like name(s) in consciousness.txt not in code:"
    ]
    for name in phantoms:
        lines.append(f"  - `{name}`  *(renamed, removed, or typo?)*")
    return Status.WARN, lines


def check_coverage(
    code_tools: Set[str],
    consciousness_path: Path,
    triggers_path: Optional[Path] = None,
    threshold: float = COVERAGE_WARN_THRESHOLD,
) -> Tuple[Status, List[str]]:
    """WARN if < threshold fraction of code tools are mentioned."""
    cons_text = _load_consciousness_text(consciousness_path)
    if cons_text is None:
        return Status.WARN, ["[warn] consciousness.txt not found — coverage unknown"]

    full_text = cons_text
    if triggers_path is not None:
        tr_text = _read_safe(triggers_path)
        if tr_text:
            full_text += "\n" + tr_text

    if not code_tools:
        return Status.PASS, ["[ok] No code tools to check"]

    mentioned = _extract_mentioned_tools(full_text, code_tools)
    ratio = len(mentioned) / len(code_tools)
    pct   = int(ratio * 100)

    if ratio < threshold:
        warn_pct = int(threshold * 100)
        return Status.WARN, [
            f"[warn] Coverage {pct}% ({len(mentioned)}/{len(code_tools)} tools) "
            f"is below {warn_pct}% threshold"
        ]
    return Status.PASS, [
        f"[ok] Coverage {pct}% ({len(mentioned)}/{len(code_tools)} tools mentioned)"
    ]


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    """Run all consciousness-capability sync checks; write report; return Status."""
    consciousness = root / CONSCIOUSNESS_PATH
    triggers      = root / TRIGGERS_PATH

    # Load code tools — BLOCKED if import fails
    code_tools, err = _load_code_tools(root)
    if code_tools is None:
        summary = (
            "## Summary\n\n"
            "- Overall: **BLOCKED**\n"
            f"- Could not load tool definitions: {err}\n\n"
        )
        write_report(REPORT_FILE, summary, Status.BLOCKED)
        return Status.BLOCKED

    triggers_arg = triggers if triggers.exists() else None

    checks = [
        ("## 1. Missing Tools (code -> consciousness)",
         lambda: check_missing_tools(code_tools, consciousness, triggers_arg)),
        ("## 2. Phantom Tools (consciousness -> code)",
         lambda: check_phantom_tools(code_tools, consciousness)),
        ("## 3. Coverage Ratio",
         lambda: check_coverage(code_tools, consciousness, triggers_arg)),
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

    n_tools = len(code_tools)
    verdict = (
        f"Consciousness is fully in sync with {n_tools} code tools."
        if overall == Status.PASS
        else "**Sync gap detected** — review missing tools and update `prompts/consciousness.txt`."
        if overall in (Status.WARN, Status.FAIL)
        else "Could not fully check sync — see details above."
    )

    summary = (
        "## Summary\n\n"
        f"- Overall: **{overall.value}**\n"
        f"- Code tools: {n_tools}\n"
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
        print(f"[consciousness_capability_sync] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")

    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
