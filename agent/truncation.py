"""Message-list helpers — safe truncation, smart compression, readable extraction."""
from typing import List, Dict, Optional


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


def compress_messages_with_groq(
    messages: List[Dict],
    groq_client,
    threshold: int = 30,
    keep_recent: int = 12,
) -> List[Dict]:
    """Compress old messages into a summary when history grows large.

    When len(messages) >= threshold, the oldest (len - keep_recent) messages
    are summarised by Groq into a single synthetic user message, and only the
    keep_recent most-recent messages are kept.  The resulting list is always
    safe to pass to the Anthropic API (no orphaned tool_result blocks).

    Returns the original list unchanged on any error so the caller is never
    left with an empty history.

    Args:
        messages:     The full message list.
        groq_client:  Initialised groq.Groq instance (free, no Claude cost).
        threshold:    Minimum list length before compression runs.
        keep_recent:  How many recent messages to preserve verbatim.

    Returns:
        Compressed message list (new list, input not mutated).
    """
    if len(messages) < threshold:
        return list(messages)

    to_compress = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    # Build readable context from the old messages
    context_lines = []
    for msg in to_compress:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, str):
            context_lines.append(f"{role}: {content[:400]}")
        elif isinstance(content, list):
            for block in content:
                if hasattr(block, "text"):
                    context_lines.append(f"{role}: {block.text[:400]}")
    context = "\n".join(context_lines)

    if not context.strip():
        return list(messages)

    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": (
                    "Summarise the following conversation history in 3-5 bullet "
                    "points. Focus on decisions made, facts established, and "
                    "context that will help continue the conversation:\n\n"
                    + context
                ),
            }],
            max_tokens=300,
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[Pi] Message compression failed (non-fatal): {e}")
        return list(messages)

    summary_msg = {
        "role": "user",
        "content": f"[CONVERSATION SUMMARY — earlier context compressed]\n{summary}",
    }
    # Ensure the list starts with a plain user message (API requirement)
    compressed = [summary_msg] + list(recent)
    return truncate_messages_safely(compressed, max_messages=keep_recent + 2)


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
