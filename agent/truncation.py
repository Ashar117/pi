"""Message-list helpers — safe truncation and readable extraction.

Both functions are pure: they take a messages list, return a new list/string,
do not mutate input. Mechanical lift from PiAgent._truncate_messages_safely
and _extract_text_from_messages (Phase 4) — no behaviour change.
"""
from typing import List, Dict


def truncate_messages_safely(messages: List[Dict], max_messages: int = 20) -> List[Dict]:
    """T-012: Bound message history without orphaning tool_result blocks.

    Walk forward from the naive slice point to a plain user text message,
    so the slice never lands inside a tool_use / tool_result pair (which
    would 400 the Anthropic API on the next call).

    Returns a new list (does not mutate input).
    """
    if len(messages) <= max_messages:
        return list(messages)
    start = len(messages) - max_messages
    while start < len(messages):
        msg = messages[start]
        if msg["role"] == "user" and isinstance(msg.get("content"), str):
            break
        start += 1
    return messages[start:]


def extract_text_from_messages(messages: List[Dict], n: int = 10) -> str:
    """Extract readable text from a messages list for Groq context.

    Handles three content shapes: plain str, list of SDK blocks (.text), and
    list of dict blocks (e.g., {"type": "tool_result", "content": "..."}).
    """
    lines = []
    for msg in messages[-n:]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            lines.append(f"{role}: {content[:300]}")
        elif isinstance(content, list):
            for block in content:
                if hasattr(block, "text"):
                    lines.append(f"{role}: {block.text[:300]}")
                elif isinstance(block, dict) and block.get("type") == "tool_result":
                    lines.append(f"tool_result: {str(block.get('content', ''))[:100]}")
    return "\n".join(lines)
