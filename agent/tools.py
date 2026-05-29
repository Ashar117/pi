"""Tool schemas and dispatch — what Claude is allowed to call and how to execute it.

T-083 R2.1: dispatch is migrating from a 1681-line if/elif ladder to a registry
of ToolSpec instances exported by each tool module. During R2.1 a module is
migrated one commit at a time; until step 4 lands, execute_tool checks the
registry first and falls back to the legacy ladder for tools that haven't
been migrated yet. get_tool_definitions prepends registry-sourced schemas
and skips any name the registry already owns.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

from agent.tool_spec import ToolSpec


# Modules whose TOOLS export feeds the registry. Order is the surface order
# the planner sees (matches the legacy hardcoded list). Modules listed but
# not yet exporting TOOLS are simply skipped.
_TOOL_MODULES: tuple = (
    "tools.tools_memory",
    "tools.tools_execution",
    "tools.tools_awareness",
    "tools.tools_project",
    "tools.tools_web",
    "tools.tools_obsidian",
    "tools.tools_image",
    "tools.tools_video_gen",
    "tools.tools_gmail",
    "tools.tools_calendar",
    "tools.tools_briefing",
    "tools.tools_media",
    "tools.tools_browse",
    "tools.tools_browser_auto",
    "tools.tools_computer_use",
    "tools.tools_telegram",
    "tools.tools_tts",
    "tools.tools_stt",
    "tools.tools_scheduler",
    "agent.watchers",  # WatcherManager TOOLS export (T-083 step 3 batch E)
)

_REGISTRY_CACHE: Optional[Dict[str, ToolSpec]] = None


def _registry() -> Dict[str, ToolSpec]:
    """Lazy import + merge of every tool module's TOOLS export.

    First call imports each module and caches the merged dict. tools_memory
    is already eagerly imported by pi_agent at startup, so its registration
    cost is zero; the heavier modules (browser_auto, computer_use) only
    incur import cost the first time a tool is dispatched.
    """
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    import importlib
    reg: Dict[str, ToolSpec] = {}
    for modname in _TOOL_MODULES:
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        for spec in getattr(mod, "TOOLS", []):
            assert spec.name not in reg, f"duplicate tool: {spec.name}"
            reg[spec.name] = spec
            for alias in spec.aliases:
                assert alias not in reg, f"duplicate alias: {alias}"
                reg[alias] = spec
    _REGISTRY_CACHE = reg
    return reg


# T-083 step 4: _LazyTool + module-level proxies (_web, _project, _obsidian,
# _gmail, _calendar, _tts, _stt, _browser, _cu, _media) were the lazy-import
# layer for the legacy elif ladder (T-064). After the elif ladder was
# deleted in step 4, each tool module now owns its own lazy instantiation
# (via _b()/_cu()/_gmail_inst() singletons in the per-module handler files).
# The proxies + factories were removed; the T-064 cold-start win is
# preserved because the registry only imports each tool module on first
# get_tool_definitions() or execute_tool() call.

_ROOT = Path(__file__).parent.parent


def _system_introspect(agent, memory_override=None) -> Dict:
    """Read live system state and return a structured dict.

    Never raises — individual failures are captured as None values so the caller
    always gets a complete (if partial) result. `memory_override` lets god mode
    introspect its private DB instead of `agent.memory` (T-082).
    """
    result: Dict = {}

    # evolution.jsonl — total interactions
    lines: list = []
    try:
        evo_path = _ROOT / "logs" / "evolution.jsonl"
        lines = [l for l in evo_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        result["total_interactions"] = len(lines)
    except Exception:
        result["total_interactions"] = None

    try:
        now = datetime.now(timezone.utc)
        last_7_ok = 0
        for l in lines:
            rec = json.loads(l)
            if rec.get("success") is not True:
                continue
            ts_str = rec.get("timestamp", "2000-01-01T00:00:00+00:00")
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts).days <= 7:
                last_7_ok += 1
        result["last_7d_successes"] = last_7_ok
    except Exception:
        result["last_7d_successes"] = None

    # tickets
    try:
        open_dir = _ROOT / "tickets" / "open"
        result["open_ticket_count"] = len(list(open_dir.glob("*.json")))
    except Exception:
        result["open_ticket_count"] = None

    try:
        closed_dir = _ROOT / "tickets" / "closed"
        result["closed_ticket_count"] = len(list(closed_dir.glob("*.json")))
    except Exception:
        result["closed_ticket_count"] = None

    # solutions
    try:
        sol_path = _ROOT / "solutions" / "SOLUTIONS.jsonl"
        sol_lines = [l for l in sol_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        result["solution_count"] = len(sol_lines)
        result["last_solution_id"] = json.loads(sol_lines[-1]).get("id") if sol_lines else None
    except Exception:
        result["solution_count"] = None
        result["last_solution_id"] = None

    # SQLite — L3 entry count
    try:
        _mem = memory_override or agent.memory
        conn = sqlite3.connect(str(_mem.sqlite_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM l3_cache")
        result["l3_entry_count"] = cursor.fetchone()[0]
        conn.close()
    except Exception:
        result["l3_entry_count"] = None

    # session / process info
    result["session_id"] = agent.session_id
    result["mode"] = agent.mode
    try:
        result["uptime_seconds"] = round(
            (datetime.now(timezone.utc) - agent.session_start).total_seconds()
        )
    except Exception:
        result["uptime_seconds"] = None

    # silent-failure ledger (T-103)
    try:
        from agent.observability import recent_failures
        result["silent_failures_24h"] = recent_failures(24)
    except Exception:
        result["silent_failures_24h"] = None

    return result


def get_tool_definitions() -> List[Dict]:
    """Return the tool schemas Claude sees in root mode.

    R2.1 transitional: schemas from the registry come first (one per
    canonical ToolSpec name, aliases filtered out), then any legacy
    hardcoded schemas whose names are NOT yet in the registry. As
    modules migrate to export TOOLS, their hardcoded entries below
    are deleted; eventually this function is a one-liner.
    """
    reg = _registry()
    # Registry schemas (canonical names only — aliases share a spec instance)
    seen_specs = set()
    registry_defs: List[Dict] = []
    for spec in reg.values():
        if id(spec) in seen_specs:
            continue
        seen_specs.add(id(spec))
        registry_defs.append({
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        })

    legacy_defs: List[Dict] = []
    # Drop any legacy entry whose name is now owned by the registry —
    # otherwise a migrated tool would appear twice in get_tool_definitions().
    return registry_defs + [d for d in legacy_defs if d["name"] not in reg]


def _validate_tool_input(spec, tool_input: Dict, tool_name: str):
    """Validate tool_input against spec.input_schema. Returns list of error dicts or [].

    Cached per ToolSpec id — schema compilation is paid once.
    """
    try:
        import jsonschema
        validator = _VALIDATORS.get(id(spec))
        if validator is None:
            validator = jsonschema.Draft7Validator(spec.input_schema)
            _VALIDATORS[id(spec)] = validator
        errors = [
            {"path": ".".join(str(p) for p in e.absolute_path) or e.json_path,
             "message": e.message}
            for e in validator.iter_errors(tool_input)
        ]
        return errors
    except Exception:
        return []  # validation is best-effort; don't block dispatch on framework error


_VALIDATORS: Dict[int, Any] = {}


def execute_tool(agent, tool_name: str, tool_input: Dict, memory_override=None) -> Any:
    """Execute a tool by name and track per-tool pattern stats.

    Operates on the PiAgent instance to access memory/execution/evolution
    subsystems. Mechanical lift from PiAgent._execute_tool — same dispatch
    table, same success-flag logic, same evolution.track_pattern call.

    T-082: `memory_override` lets per-mode callers (god) route memory tool
    calls through a private MemoryTools instance (different DB / namespace)
    without mutating `agent.memory`. When None, falls back to `agent.memory`.

    T-083 R2.1: registry dispatch runs first. If the name is owned by a
    ToolSpec, dispatch via spec.handler + spec.success_predicate; the
    legacy elif ladder is only reached for tools whose owning module
    has not yet migrated.

    T-107: tool_input is validated against spec.input_schema before dispatch.
    Schema mismatches return a structured error the LLM can self-correct from.
    """
    start_time = datetime.now(timezone.utc)

    spec = _registry().get(tool_name)
    if spec is None:
        return {"error": f"unknown tool: {tool_name}", "success": False}

    # T-107: validate inputs before dispatch
    validation_errors = _validate_tool_input(spec, tool_input, tool_name)
    if validation_errors:
        from agent.observability import track_silent
        track_silent(
            "tools.invalid_input",
            ValueError(f"{tool_name}: {validation_errors[0]['message']}"),
            context={"tool": tool_name, "errors": validation_errors},
        )
        return {
            "error": "invalid_input",
            "tool": tool_name,
            "schema_mismatch": validation_errors,
            "expected_schema": spec.input_schema,
            "success": False,
        }

    try:
        result = spec.handler(agent, tool_input, memory_override=memory_override)
        success = bool(spec.success_predicate(result))
    except Exception as e:
        result = {"error": str(e), "tool": tool_name, "success": False}
        success = False

    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    agent.evolution.track_pattern(
        pattern_name=f"tool_{tool_name}",
        success=success,
        metadata={"duration_seconds": duration},
    )
    return result

