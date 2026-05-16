# ADR-002 — Tool dispatch becomes a registry of `ToolSpec`

**Status:** Proposed (awaiting Ash sign-off)
**Date:** 2026-05-16
**Ticket:** [T-083](../../tickets/open/T-083-r2-tool-registry-and-consolidation.json) (R2 of Hardening Track)
**Author:** Claude (Opus 4.7)

---

## Decision

Each `tools/tools_*.py` module exports a module-level `TOOLS: list[ToolSpec]`.
`agent/tools.py` discovers those exports, builds the schema list, and
dispatches tool calls via a dict lookup. The 1681-line `if tool_name == "X":`
ladder is replaced by a registry pattern.

Concretely:

- New `agent/tool_spec.py` defines a frozen dataclass:
  ```python
  @dataclass(frozen=True)
  class ToolSpec:
      name: str
      description: str
      input_schema: dict
      handler: Callable[..., Any]
      success_predicate: Callable[[Any], bool] = lambda r: True
  ```
- Each tool module ends with `TOOLS = [ToolSpec(...), ToolSpec(...), ...]`.
- `agent/tools.py` does two jobs only: (1) import each tool module's `TOOLS`,
  (2) provide `get_tool_definitions()` and `execute_tool(agent, name, input, *, memory_override=None)`
  that consume the merged registry. Target shrink: 1681 → ~150 lines.
- Handler signature stays small: `handler(agent, tool_input, *, memory_override=None) -> Any`.
  Returns the natural shape — list/dict/string per tool. Success is decided
  by the spec's `success_predicate`, not by the handler.
- `_LazyTool` proxies preserved unchanged; module-level imports remain lazy.

R2 ships in three phases:
- **R2.1 — registry shape only, no merges.** All 17 modules gain `TOOLS`.
  Tool count stays at 73. `agent/tools.py` drops to ~150 lines.
- **R2.2 — consolidation, 73 → ~42.** Four mergers (A, B, C, audit D)
  collapse redundant tools; aliases preserve old names for one release cycle.
- **R2.3 — weekly invocation audit cron.** `scripts/passive/tool_usage_audit.py`
  files P3 prune tickets for <3 invocations / 30d, P2 fix tickets for
  >50% failure / >10 invocations.

## Context

`agent/tools.py` is 1681 lines:
- `get_tool_definitions()` (lines ~138–1130): one giant list literal, 73
  Anthropic tool schemas.
- `execute_tool(agent, name, input)` (lines ~1151–1672): one giant
  `if/elif name == "X":` ladder, one branch per tool, ~12 lines each.

Two problems compound:

**1. Schema/dispatch drift.** A tool's schema and its handler live in
different functions, 400+ lines apart. Adding a tool means editing both,
plus the lazy-import factory line near the top, plus the success-flag pattern
in the dispatch branch. Forgetting one is silent — the tool appears in
the schema list but doesn't dispatch, or dispatches but isn't advertised.
T-082's `memory_override` retrofit touched 7 sites for the same reason.

**2. Planner overload.** The Claude planner sees 73 tool schemas every
turn. Planning accuracy degrades past ~25–30 tools — observed empirically
in S-055 (knowledge_graph at 0 invocations over 24 days, model never
picked it) and in the day-to-day pattern of the model defaulting to
`web_search` even when `scholar_search` is the better fit. Tool sprawl
isn't only a maintenance problem; it's an inference-quality problem.

**Cluster examples from the audit (R2.2 targets):**

| Cluster | Tools | Real intent |
|---|---|---|
| Media analysis | `analyze_image`, `analyze_images`, `analyze_video`, `ocr_image`, `analyze_document_smart` | "Look at this file and tell me what's in it" |
| Web fetch | `web_browse`, `reddit_browse`, `reddit_search`, `reddit_thread`, `discord_read` | "Read this URL / thread / channel" |
| Watchers | `watcher_add`, `watcher_list`, `watcher_remove`, `watcher_status` | "Manage one watcher object" |

Each cluster trades 4–5 tool names for 1.

## The `TOOLS` export contract

Each tool module exports exactly one symbol named `TOOLS`. It is a
`list[ToolSpec]`. `ToolSpec` is frozen so missing fields raise TypeError
at import time, not silent KeyError at first invocation.

```python
# tools/tools_memory.py — end of file

from agent.tool_spec import ToolSpec

def _handle_memory_read(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    return mem.memory_read(query=tool_input["query"], tier=tool_input.get("tier"))

def _handle_memory_write(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    expiry = datetime.fromisoformat(tool_input["expiry"]) if tool_input.get("expiry") else None
    return mem.memory_write(
        content=tool_input["content"],
        tier=tool_input.get("tier", "l3"),
        importance=tool_input.get("importance", 5),
        category=tool_input.get("category", "note"),
        expiry=expiry,
        session_id=agent.session_id,
        source=tool_input.get("source", "stated"),
    )

TOOLS = [
    ToolSpec(
        name="memory_read",
        description="Search memory. Returns matching entries.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "tier": {"type": "string", "enum": ["l1", "l2", "l3"]},
            },
            "required": ["query"],
        },
        handler=_handle_memory_read,
    ),
    ToolSpec(
        name="memory_write",
        description="Write a fact to memory.",
        input_schema={...},
        handler=_handle_memory_write,
        success_predicate=lambda r: r.get("verified", False),
    ),
    ...
]
```

`agent/tools.py` becomes a thin loader:

```python
# agent/tools.py — post-R2.1, ~150 lines

import importlib
from typing import Any, Dict, List

from agent.tool_spec import ToolSpec

# Module names whose TOOLS export we discover. Module-level, ordered.
_TOOL_MODULES = (
    "tools.tools_memory",
    "tools.tools_execution",
    "tools.tools_awareness",
    "tools.tools_project",
    "tools.tools_web",
    "tools.tools_obsidian",
    "tools.tools_image",
    "tools.tools_gmail",
    "tools.tools_calendar",
    "tools.tools_media",
    "tools.tools_browse",
    "tools.tools_browser_auto",
    "tools.tools_computer_use",
    "tools.tools_telegram",
    "tools.tools_tts",
    "tools.tools_stt",
    "tools.tools_scheduler",
)

def _registry() -> dict[str, ToolSpec]:
    """Lazily import each tool module and merge its TOOLS export.

    Module imports are deferred to first call so PiAgent startup stays
    fast (preserves the T-064 lazy-import win).
    """
    if hasattr(_registry, "_cache"):
        return _registry._cache
    reg: dict[str, ToolSpec] = {}
    for modname in _TOOL_MODULES:
        mod = importlib.import_module(modname)
        for spec in getattr(mod, "TOOLS", []):
            assert spec.name not in reg, f"duplicate tool: {spec.name}"
            reg[spec.name] = spec
    _registry._cache = reg
    return reg


def get_tool_definitions() -> List[Dict]:
    """Anthropic tool schemas — one entry per registered tool."""
    return [
        {"name": s.name, "description": s.description, "input_schema": s.input_schema}
        for s in _registry().values()
    ]


def execute_tool(agent, tool_name: str, tool_input: Dict, *, memory_override=None) -> Any:
    """Dispatch via registry. Falls through to a helpful error for unknown names."""
    spec = _registry().get(tool_name)
    if spec is None:
        return {"error": f"unknown tool: {tool_name}",
                "available": list(_registry().keys())[:10] + ["..."]}
    try:
        result = spec.handler(agent, tool_input, memory_override=memory_override)
        success = spec.success_predicate(result)
    except Exception as e:
        result = {"error": str(e), "tool": tool_name}
        success = False
    agent.evolution.track_pattern(tool_name, success=success)
    return result
```

**That's the entire dispatcher.** Everything else (`if tool_name == "X":`
ladders, per-tool success flag patterns, the lazy-tool factory list) goes
away. The lazy-import behavior is preserved because `_registry()` only runs
on first `execute_tool` / `get_tool_definitions` call, not at module load.

## Alternatives considered

### A1 — Decorator-based registration

```python
@register_tool(name="memory_read", schema=...)
def memory_read(agent, tool_input): ...
```

**Pros:** "magic happens here" centralized; one decorator does everything.
**Cons:** Side-effects-at-import register pattern; import-order-dependent;
breaks lazy-import (the decorator runs at module load even if the tool is
never called); debugger jump-to-def lands inside the decorator, not the
function. Already bitten the project once with similar pattern in
`agent/watchers.py`. **Rejected.**

### A2 — Plain dicts instead of `ToolSpec` dataclass

```python
TOOLS = [
    {"name": "memory_read", "schema": {...}, "handler": ...},
    ...
]
```

**Pros:** No new module.
**Cons:** Typo-friendly (`"desc"` vs `"description"` silently works,
fails at dispatch time). No type help. Future field adds become a
73-row text-edit. **Rejected — see Q1 in session transcript.**

### A3 — YAML/JSON schema file separated from code

`tools/registry.yaml` holds names + schemas; modules export only
handlers; agent/tools.py glues by name.

**Pros:** Designers can edit schemas without touching Python.
**Cons:** Designer-shaped solution to a non-designer-shaped problem
(Ash is the only editor). Schema/handler split is exactly the drift
this ADR is trying to *kill*. **Rejected.**

### A4 — Status quo (do nothing)

**Pros:** Zero migration cost today.
**Cons:** Every tool added compounds the maintenance debt. Planner
accuracy continues to degrade at 73+ tools. R3 (router-tier work)
will need to touch `agent/tools.py` and inherit the elif-ladder pain.
**Rejected — explicitly the path R2 is here to break.**

### A5 — `ToolContext` object instead of explicit kwargs

```python
def handler(ctx: ToolContext, tool_input): ...
```

**Pros:** Future-proof for R8's ModeConfig flowthrough.
**Cons:** Speculative — most of the 73 handlers don't need per-call
state and won't, even after R8. Adding a kwarg with a default value
when R8 lands is non-breaking. **Deferred to R8 if needed; see Q3 in
session transcript.**

## Consequences

### Positive

- `agent/tools.py`: 1681 → ~150 lines. R2.1 alone hits the success criterion.
- Adding a tool is a 1-line `TOOLS.append(...)` in the owning module. No
  central file to edit.
- Schema and handler are colocated — drift becomes structurally impossible.
- The `success_predicate` slot makes telemetry semantics auditable: skim
  one file, see what each tool considers "success."
- R3 (tier param) and R8 (ModeConfig flowthrough) inherit a registry they
  can extend, not an elif ladder they have to rewrite.
- Planner: R2.2 mergers cut the schema list ~73 → ~42, lifting Claude's
  selection accuracy in the regime where this matters most.
- Tool-naming aliases survive one release cycle in the registry
  (`aliases: tuple[str, ...] = ()` field — see "Open questions" below)
  so we don't break in-flight conversations or external integrations.

### Negative / risks

- **Mechanical risk: 17 modules touched.** Mitigation: one module per
  commit during R2.1. Verify must PASS at each. Mid-migration state
  (some modules use registry, others use elif ladder) is supported by
  `execute_tool` checking the registry first and falling back to the
  elif ladder; the ladder is deleted only in step 4 once every module
  is migrated.
- **God allowlist references tool names by string.** After R2.2 merger
  renames (`analyze_image` → `analyze_media`), `agent/modes.py::MODE_CONFIGS["god"].tool_allowlist`
  needs updating. Acceptance: R2.2's solution entries name the rename
  in `files_affected`; aliases keep the old names working until the
  followup cycle.
- **Lazy-import preserved but slightly different shape.** Module imports
  inside `_registry()` happen on first call to `get_tool_definitions` or
  `execute_tool`, not at agent startup. T-064's lazy `_LazyTool` proxies
  for heavy subsystems (deepface, playwright, etc.) stay in the tool
  modules themselves. Net startup impact: neutral.
- **Test surface: any test that imports `agent.tools.X` expecting the
  old function name (`_system_introspect`, helpers) needs to either
  import from the new module or update.** Survey via grep before R2.1
  step 3; surface in the solution entry.

### Neutral

- `agent/tools.py` retains its name and import path. Existing
  `from agent.tools import execute_tool, get_tool_definitions` keeps
  working. The internals change; the public surface does not.
- The archived snapshot of pre-R2.1 `agent/tools.py` goes to
  `docs/_archive/agent_tools_v1.py` (gitignored under `docs/_archive/`
  pattern — wait, `docs/_archive/` is *not* gitignored. Two of the
  files I saw at session start, `core_knowledge_graph.py` and
  `scripts_export_kg.py`, sit in plain `docs/_archive/`. The pre-R2.1
  `tools.py` is implementation, gitignored when live. Archive it to
  `docs/_archive/_private/agent_tools_v1.py` for parity with T-082's
  god.py archival path.

## Migration plan (one step = one commit)

**R2.1 — registry shape only, no merges:**

1. **This ADR.** Sign-off before any code.
2. Create `agent/tool_spec.py` with the `ToolSpec` dataclass.
3. Pilot module: `tools/tools_memory.py` exports `TOOLS = [...]` for its
   3 tools (memory_read, memory_write, memory_delete). `agent/tools.py`
   imports + dispatches via registry; elif branches for the 3 tools
   removed from `execute_tool`. `get_tool_definitions` builds the
   memory-tools section from registry, rest from the old hardcoded list
   (transitional). `verify.py` PASS, including the existing memory
   tests.
4. Extend `TOOLS` export to the remaining 16 modules, one commit per
   module. Each commit removes its corresponding elif branches and
   schema entries from `agent/tools.py`. Pre-existing tests must
   continue to pass.
5. Once every module is migrated, `agent/tools.py` is just the
   registry loader (~150 lines). Old `_TOOL_MODULES` ladder remnants
   (lazy factories, helper functions) deleted. Archive pre-R2.1
   snapshot to `docs/_archive/_private/agent_tools_v1.py`.

**R2.2 — consolidation, four mergers (each is its own commit):**

6. Merger A: `analyze_image` + `analyze_images` + `analyze_video` +
   `ocr_image` + `analyze_document_smart` → single `analyze_media(path[s], question, kind="auto")`.
   Old names retained as aliases for one release cycle.
7. Merger B: `web_browse` + `reddit_browse` + `reddit_search` +
   `reddit_thread` + `discord_read` → `fetch(source, query_or_url, options)`.
   `daily_briefing` is kept as a separate workflow (it composes, not fetches).
8. Merger C: `watcher_add` + `watcher_list` + `watcher_remove` +
   `watcher_status` → `watcher(action, ...)`.
9. Audit D: `computer_run_task` removed from the tool registry, moved
   to `scripts/sprint.py` as a CLI command — it is a recursive agent
   loop, not a leaf tool, and exposing it as a tool encourages the
   planner to invoke it inappropriately.

**R2.3 — invocation audit cron (independent of R2.1/R2.2):**

10. `scripts/passive/tool_usage_audit.py` reads `logs/evolution.jsonl`,
    groups tool-call events by name over the last 30 days, files a
    P3 ticket for any tool with <3 invocations or a P2 ticket for any
    tool with >50% failure rate over >10 invocations. Registered as a
    Friday 02:00 local cron via the existing scheduler.

**Closeout:** S-060 (R2.1), S-061 (R2.2), S-062 (R2.3) appended to
`solutions/SOLUTIONS.jsonl`. T-083 moved to `tickets/closed/` with
`linked_solutions=["S-060", "S-061", "S-062"]`. `python scripts/refresh_pi.py`.
`CHECKPOINTS/current.md` updated. R2 unblocks R3 indirectly (router
work no longer has to step around the elif ladder).

## Open questions / follow-ups

- **Alias mechanism shape.** During R2.2 mergers, old names map to new
  names. Two options: (1) add `aliases: tuple[str, ...] = ()` to
  `ToolSpec` so `analyze_media` declares its old names; the registry
  registers each alias as an entry pointing at the same spec, and
  `get_tool_definitions` filters aliases out of the schema list. (2) A
  separate `tools/aliases.py` map. Going with (1) — aliases live with
  the spec they're aliasing, which is where you'd look to remove them.
  Confirmed during R2.2 step 6.

- **Per-tool cost predicate?** A few tools (image_gen, gmail_send) have
  external API costs that the cost tracker should know about per-call.
  Out of scope for R2; file as a separate ticket if it ever becomes a
  real budget concern.

- **Versioned schemas?** If a tool's input_schema changes
  backwards-incompatibly, do we need a `schema_version` field?
  YAGNI today; the project doesn't ship versioned APIs to external
  consumers. Revisit if Phase 9 (distributed) introduces remote tool
  invocation.

## Sign-off

- [ ] Ash — read and agree before step 2 begins.
