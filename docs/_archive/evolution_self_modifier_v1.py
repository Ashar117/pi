"""docs/_archive/evolution_self_modifier_v1.py

Archived from evolution.py:294-389 on 2026-05-17 per T-088 (R7 of the
Hardening Track). Zero callers in the codebase as of removal; dates back
to Phase 5's "Pi modifies its own prompts and writes new tools at runtime"
ambition. The autonomous-improvement goal now lives in scripts/sprint.py
where edits happen through the LLM + diff review path, not a programmatic
prompt-rewriter.

Why archived rather than evolved:

- modify_consciousness() does a literal `content.replace(section, new_content)`
  against prompts/consciousness.txt. The "section" arg is a unique-substring
  match — if the substring drifts (a comment edit, a typo fix), the
  programmatic edit silently no-ops and returns success.
- add_tool() writes raw Python to tools/tool_<name>.py with no syntax
  validation, no agent/tools.py registration, no verify.py gate. A malformed
  generated file breaks agent/tools.py imports and the daemon enters a
  restart loop.
- The whole class is an "attractive nuisance" — future-Pi reading the code
  could wire LLM output into modify_consciousness() and produce an
  unbootable daemon by the time the user notices.

If self-modification returns, the design must include: (a) write proposed
edits to tools/_proposed/ (gitignored), (b) require `python scripts/verify.py
PASS` against the proposed state, (c) require an explicit human approval
flag, (d) atomic swap-or-revert backed by git. None of that is in v1.

This file is kept for context — never re-imported. evolution.py keeps
EvolutionTracker (the live class).
"""

import os
from datetime import datetime
from typing import Dict


class SelfModifier:
    """
    Allows Pi to modify its own code/prompts.
    """

    def __init__(self, project_root: str = None):
        if project_root is None:
            project_root = os.path.dirname(os.path.abspath(__file__))
        self.project_root = project_root

    def modify_consciousness(self, section: str, new_content: str, backup: bool = True) -> Dict:
        """
        Modify consciousness prompt.

        Args:
            section: Which section to modify (must be unique)
            new_content: New content for that section
            backup: Create backup first

        Returns:
            {"success": bool, "backup_path": str}
        """

        consciousness_path = os.path.join(self.project_root, "prompts", "consciousness.txt")

        if not os.path.exists(consciousness_path):
            return {"success": False, "error": "Consciousness file not found"}

        # Backup
        if backup:
            backup_path = consciousness_path + f".backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            with open(consciousness_path, 'r') as f:
                content = f.read()
            with open(backup_path, 'w') as f:
                f.write(content)
        else:
            backup_path = None

        # Modify
        try:
            with open(consciousness_path, 'r') as f:
                content = f.read()

            if section not in content:
                return {
                    "success": False,
                    "error": f"Section '{section}' not found in consciousness"
                }

            # Replace section
            new_content_full = content.replace(section, new_content)

            with open(consciousness_path, 'w') as f:
                f.write(new_content_full)

            return {
                "success": True,
                "backup_path": backup_path,
                "message": "Consciousness updated"
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def add_tool(self, tool_name: str, tool_code: str) -> Dict:
        """
        Add new tool to Pi's capabilities.

        Args:
            tool_name: Name of new tool
            tool_code: Python code for tool

        Returns:
            {"success": bool}
        """

        tools_path = os.path.join(self.project_root, "tools", f"tool_{tool_name}.py")

        try:
            with open(tools_path, 'w') as f:
                f.write(tool_code)

            return {
                "success": True,
                "path": tools_path,
                "message": f"Tool '{tool_name}' created"
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
