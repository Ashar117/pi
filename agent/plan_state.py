"""T-183: Lightweight per-session plan state for plan-then-execute mode.

A plan is a list of steps (text + status). It lives in the dynamic system prompt
segment so it survives message-history compaction — the model always sees its
remaining steps even after the context window rolls.

Usage:
    plan = PlanState()
    plan.set(["Read the file", "Edit the function", "Run tests"])
    plan.update(0, "done")
    print(plan.render())  # injects into dynamic_p

Plans clear on /newchat and at session exit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional

_MAX_TOKENS = 150  # hard cap: ~600 chars at 4 chars/token

Status = Literal["pending", "done", "failed", "skipped"]
_STATUS_ICON = {"pending": "○", "done": "✓", "failed": "✗", "skipped": "–"}


@dataclass
class PlanStep:
    text: str
    status: Status = "pending"


class PlanState:
    """Session-scoped plan; injects into dynamic prompt segment."""

    def __init__(self) -> None:
        self._steps: List[PlanStep] = []

    # ── Mutations ─────────────────────────────────────────────────────────────

    def set(self, steps: List[str]) -> None:
        """Replace the current plan with a new list of step descriptions."""
        self._steps = [PlanStep(text=s.strip()) for s in steps if s.strip()]

    def update(self, index: int, status: Status, text: Optional[str] = None) -> bool:
        """Update step[index] status (and optionally text). Returns False if OOB."""
        if index < 0 or index >= len(self._steps):
            return False
        self._steps[index].status = status
        if text is not None:
            self._steps[index].text = text.strip()
        return True

    def clear(self) -> None:
        self._steps = []

    # ── Rendering ─────────────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        return not self._steps

    def render(self) -> str:
        """Return a compact multi-line block for injection into dynamic_p."""
        if not self._steps:
            return ""
        lines = ["── ACTIVE PLAN ──────────────────────────────────────────────────"]
        for i, step in enumerate(self._steps):
            icon = _STATUS_ICON.get(step.status, "?")
            lines.append(f"  [{icon}] {i}. {step.text}")
        lines.append("────────────────────────────────────────────────────────────────")
        block = "\n".join(lines)
        # Hard cap
        if len(block) > _MAX_TOKENS * 4:
            block = block[: _MAX_TOKENS * 4 - 3] + "…"
        return block

    # ── Serialisation (for /newchat reset, not persistence) ──────────────────

    def to_dict(self) -> dict:
        return {"steps": [{"text": s.text, "status": s.status} for s in self._steps]}

    @classmethod
    def from_dict(cls, data: dict) -> "PlanState":
        obj = cls()
        for item in data.get("steps", []):
            step = PlanStep(text=item.get("text", ""), status=item.get("status", "pending"))
            obj._steps.append(step)
        return obj

    def __len__(self) -> int:
        return len(self._steps)
