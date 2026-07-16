"""core/roles.py — Role-based pipeline abstraction (T-207 / ADR-???).

A Role is a named LLM persona that runs at a specific tier with a specific
system framing. A RolePipeline runs roles sequentially over a shared
scratchpad dict, threading each role's output as context for the next.

This replaces the hardcoded 3-agent debate in core/research_mode.py with a
general abstraction. The 'careful_answer' pipeline is the first concrete use;
research_mode is reimplemented as a RolePipeline behind its existing interface.

Pipeline outputs are logged to logs/roles/ per run for inspection.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
_ROLES_LOG = _ROOT / "logs" / "roles"

# Max size of the scratchpad string passed between roles (token safety).
_SCRATCHPAD_MAX_CHARS = 6000


@dataclass
class Role:
    """A named LLM persona for one step in a pipeline.

    Attributes:
        name:            Unique identifier (used as scratchpad key).
        router_tier:     Provider tier passed to LLMRouter.chat() — 'cheap',
                         'balanced', 'premium', etc.
        system_framing:  Appended to the base system prompt for this role.
        max_tokens:      Max tokens for this role's response.
    """
    name: str
    router_tier: str
    system_framing: str
    max_tokens: int = 1024


@dataclass
class RolePipeline:
    """Run a sequence of Roles over a shared scratchpad.

    Usage:
        result = pipeline.run(prompt, router, base_system="…")
        print(result["final"])   # last role's output
    """
    name: str
    roles: List[Role]

    def run(
        self,
        prompt: str,
        router: Any,
        base_system: str = "",
        on_role_done: Optional[Callable[[str, str], None]] = None,
    ) -> Dict[str, Any]:
        """Execute roles sequentially.

        Args:
            prompt:        The initial user question/task.
            router:        LLMRouter instance (or any object with a .chat() method).
            base_system:   Prepended to each role's system_framing.
            on_role_done:  Optional callback(role_name, role_output) called after
                           each role completes — useful for progress indicators.

        Returns dict with:
            final:        Last role's text output.
            scratchpad:   Full {role_name: text} dict.
            role_outputs: List of {role, text, tokens_in, tokens_out, cost_usd}.
            pipeline:     This pipeline's name.
        """
        scratchpad: Dict[str, str] = {}
        role_outputs: List[Dict] = []

        for role in self.roles:
            system = (
                f"{base_system}\n\n{role.system_framing}".strip()
                if base_system
                else role.system_framing
            )
            messages = _build_role_messages(prompt, scratchpad, role)

            resp = router.chat(
                messages=messages,
                system=system,
                max_tokens=role.max_tokens,
                tier=role.router_tier,
            )

            scratchpad[role.name] = resp.text
            role_outputs.append({
                "role": role.name,
                "text": resp.text,
                "tokens_in": resp.tokens_in,
                "tokens_out": resp.tokens_out,
                "cost_usd": getattr(resp, "cost_usd", 0.0),
                "provider": resp.provider,
            })

            if on_role_done:
                on_role_done(role.name, resp.text)

        final = scratchpad.get(self.roles[-1].name, "") if self.roles else ""
        result = {
            "final": final,
            "scratchpad": scratchpad,
            "role_outputs": role_outputs,
            "pipeline": self.name,
        }

        _log_run(self.name, prompt, result)
        return result


def _build_role_messages(
    prompt: str,
    scratchpad: Dict[str, str],
    role: Role,
) -> List[Dict]:
    """Build message list for a role turn.

    First role: just the original prompt.
    Subsequent roles: prompt + truncated scratchpad from prior roles.
    """
    if not scratchpad:
        return [{"role": "user", "content": prompt}]

    prior = "\n\n".join(
        f"[{name}]:\n{text}" for name, text in scratchpad.items()
    )
    if len(prior) > _SCRATCHPAD_MAX_CHARS:
        prior = prior[:_SCRATCHPAD_MAX_CHARS] + "\n…[truncated]"

    content = (
        f"Original question:\n{prompt}\n\n"
        f"Prior analysis:\n{prior}\n\n"
        f"Now apply your role ({role.name}) to the above."
    )
    return [{"role": "user", "content": content}]


def _log_run(pipeline_name: str, prompt: str, result: Dict) -> None:
    """Append a run record to logs/roles/<pipeline>.jsonl (non-fatal)."""
    try:
        _ROLES_LOG.mkdir(parents=True, exist_ok=True)
        path = _ROLES_LOG / f"{pipeline_name}.jsonl"
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "prompt_len": len(prompt),
            "roles": [r["role"] for r in result.get("role_outputs", [])],
            "total_cost": sum(r.get("cost_usd", 0) for r in result.get("role_outputs", [])),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ── Built-in pipelines ────────────────────────────────────────────────────────

CAREFUL_ANSWER_PIPELINE = RolePipeline(
    name="careful_answer",
    roles=[
        Role(
            name="planner",
            router_tier="cheap",
            system_framing=(
                "You are a planning assistant. Given the user's question, identify "
                "the 2-3 key sub-questions to answer, any important caveats, and "
                "the right approach. Be concise (≤200 words)."
            ),
            max_tokens=300,
        ),
        Role(
            name="drafter",
            router_tier="balanced",
            system_framing=(
                "You are a drafting assistant. Using the planner's analysis, write "
                "a thorough, accurate answer to the original question."
            ),
            max_tokens=1024,
        ),
        Role(
            name="critic",
            router_tier="cheap",
            system_framing=(
                "You are a critic. Review the draft answer for errors, missing "
                "caveats, or unsupported claims. Point out specific issues or "
                "confirm the draft is sound. Be brief (≤150 words)."
            ),
            max_tokens=200,
        ),
    ],
)

RESEARCH_DEBATE_PIPELINE = RolePipeline(
    name="research_debate",
    roles=[
        Role(
            name="claude",
            router_tier="premium",
            system_framing=(
                "You are Claude, a careful and nuanced analyst. "
                "Give your perspective on the question, citing key considerations."
            ),
            max_tokens=800,
        ),
        Role(
            name="fast_model",
            router_tier="fast",
            system_framing=(
                "You are a fast analytical model. Review Claude's perspective, "
                "then add what it missed, challenge its assumptions, and give your "
                "own take. Be direct and efficient."
            ),
            max_tokens=800,
        ),
        Role(
            name="synthesiser",
            router_tier="balanced",
            system_framing=(
                "You are a synthesiser. Two analysts have weighed in on the "
                "question. Produce a final, integrated answer that incorporates "
                "the strongest points from both perspectives. Be authoritative."
            ),
            max_tokens=1200,
        ),
    ],
)
