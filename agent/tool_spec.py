"""agent/tool_spec.py — ToolSpec dataclass for the tool registry (T-083 R2.1).

Each tools/tools_*.py module exports a module-level TOOLS list of these.
agent/tools.py discovers them and dispatches via dict lookup.

Frozen so a missing required field raises TypeError at import time instead
of silent KeyError at first invocation. The `success_predicate` slot keeps
per-tool telemetry semantics colocated with the spec; handlers return their
natural result shape and stay free of telemetry knowledge. `aliases` lets
renamed tools (R2.2 mergers) keep dispatching for one release cycle without
appearing in get_tool_definitions().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Tuple


def _always_succeeds(_result: Any) -> bool:
    return True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Dict
    handler: Callable[..., Any]
    success_predicate: Callable[[Any], bool] = field(default=_always_succeeds)
    aliases: Tuple[str, ...] = ()
