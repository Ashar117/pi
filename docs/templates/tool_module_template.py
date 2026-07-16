"""
tools/tools_TOOLNAME.py — Toolsmith template (T-208 / ADR-008).

Copy this file to tools/tools_TOOLNAME.py, replace every TOOLNAME with the
real tool name (snake_case), fill in the TODOs, then run:

    python -m pytest testing/test_tools_TOOLNAME.py -v
    # add "tools.tools_TOOLNAME" to agent/tools.py _TOOL_MODULES
    python scripts/verify.py --quiet
"""

from __future__ import annotations

from typing import Any, Dict

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tool_spec import ToolSpec  # noqa: E402


# ── Tool logic ────────────────────────────────────────────────────────────────

def _do_toolname(param: str) -> Dict[str, Any]:
    """TODO: core logic — no agent context needed here."""
    return {"result": param, "success": True}


# ── Handler ───────────────────────────────────────────────────────────────────

def _handle_toolname(agent: Any, tool_input: Dict, *, memory_override=None) -> Dict:
    """Handler called by agent.tools.execute_tool()."""
    param = tool_input.get("param", "")
    if not param:
        return {"success": False, "error": "param is required"}
    return _do_toolname(param)


# ── Registration ──────────────────────────────────────────────────────────────

TOOLS = [
    ToolSpec(
        name="toolname",  # TODO: globally unique snake_case name
        description=(
            "TODO: one or two sentences: what the tool does and when to call it. "
            "Vague descriptions produce wrong call-site decisions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "param": {
                    "type": "string",
                    "description": "TODO: describe this parameter",
                },
            },
            "required": ["param"],
        },
        handler=_handle_toolname,
        # serial=True   # uncomment if the tool has side effects (writes/sends)
    ),
]
