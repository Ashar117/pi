"""Canonical conversation turn type + single text extraction point (T-161).

ALL text extraction from message content must route through `message_text`.
Never scatter inline `isinstance(content, list)` / `b.get("text")` through
the agent layer — those duplications are the root of the write/read divergence
bug class (see T-148, docs/PROJECT_MAP.md).

Provider-specific shaping (Anthropic blocks ↔ Groq strings) happens ONLY at
the router boundary (core/providers/, core/schema_translate.py). Never here.
"""
from __future__ import annotations

from typing import List, Optional, TypedDict

from agent.truncation import _block_text  # noqa: F401  (re-exported as canonical extractor)


class Turn(TypedDict, total=False):
    """Canonical shape of one entry in PiAgent.self.messages.

    Fields
    ------
    role            "user" | "assistant"
    content         str for plain-text turns; list[dict] (Anthropic block list)
                    for assistant turns that may carry tool_use/tool_result.
    mode            Pi mode active when the turn was recorded.
    conversation_id The conversation this turn belongs to (T-142).
    ts              ISO-8601 UTC timestamp of the turn.
    """
    role: str
    content: "str | list"
    mode: Optional[str]
    conversation_id: Optional[str]
    ts: Optional[str]


def message_text(msg: dict) -> str:
    """Return the human-readable text of a message dict.

    Handles both content shapes that appear in self.messages:
      - str  — plain user input or normie/shortcut assistant replies.
      - list — Anthropic block list ({"type":"text",...} entries from root).

    Only text-type blocks are included; tool_use and tool_result blocks are
    skipped so vault archives and handoffs stay clean. If you need tool_result
    text (e.g. for compression context), call _block_text directly on each block.

    Never returns None. Returns "" when the message carries no readable text.
    """
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    parts.append(t)
        return " ".join(parts)
    return ""


from contextlib import contextmanager


@contextmanager
def conversation_switch(agent, target_conv_id: str, max_turns: int = 40):
    """Context manager: save agent context, switch to target_conv_id, restore on exit.

    Guarantees that agent.conversation_id and agent.messages are restored to
    their pre-call state even if an exception is raised inside the block.
    T-188/T-206 pattern — every autonomous turn uses this so it never splices
    into Ash's active thread.

    Usage:
        with conversation_switch(agent, "telegram:42"):
            reply = agent.process_input(user_text)
    """
    from datetime import datetime, timezone
    from agent.truncation import truncate_messages_safely

    saved_conv_id = agent.conversation_id
    saved_messages = list(agent.messages)
    try:
        # Ensure conversation row exists
        try:
            agent.memory.create_conversation(
                target_conv_id,
                agent.mode,
                datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            pass

        # Load turns for the target conversation
        if target_conv_id != agent.conversation_id:
            try:
                turns = agent.memory.load_conversation_turns(target_conv_id, max_turns=max_turns)
                agent.messages = truncate_messages_safely(turns, max_messages=20) if turns else []
            except Exception:
                agent.messages = []
            agent.conversation_id = target_conv_id

        yield agent
    finally:
        agent.conversation_id = saved_conv_id
        agent.messages = saved_messages
