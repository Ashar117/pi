"""Message-list helpers — safe truncation, smart compression, readable extraction."""
from typing import List, Dict, Optional, Any


class CompressionFailed(Exception):
    """Raised when all compression LLMs fail; carries the original messages list."""
    def __init__(self, original_messages: List[Dict]):
        super().__init__("All compression providers failed")
        self.original_messages = original_messages


def truncate_messages_safely(messages: List[Dict], max_messages: int = 20) -> List[Dict]:
    """T-012: Bound message history without orphaning tool_result blocks.

    Walk forward from the naive slice point to a plain user text message,
    so the slice never lands inside a tool_use / tool_result pair (which
    would 400 the Anthropic API on the next call).

    Returns a new list (does not mutate input).
    """
    if len(messages) <= max_messages:
        return list(messages)
    naive = len(messages) - max_messages

    # Walk FORWARD from the naive cut to the next plain-string user message,
    # so the slice never orphans a tool_use / tool_result pair.
    start = naive
    while start < len(messages):
        msg = messages[start]
        if msg["role"] == "user" and isinstance(msg.get("content"), str):
            return messages[start:]
        start += 1

    # T-148: forward walk found no safe boundary in the tail (e.g. a long
    # tool-only stretch). The old code returned messages[len:] == [] here,
    # wiping all history. Instead walk BACKWARD from the naive cut for the
    # most recent safe boundary — keeps strictly more context, never empty.
    start = naive
    while start >= 0:
        msg = messages[start]
        if msg["role"] == "user" and isinstance(msg.get("content"), str):
            return messages[start:]
        start -= 1

    # No plain-string user message anywhere: return the full list rather than
    # an empty one. Truncation is a safety bound, not a hard guarantee.
    return list(messages)


# T-150: preserve high-value tokens verbatim instead of crushing to "3-5 bullets".
_COMPRESS_PROMPT = (
    "Summarise the conversation below so it can be continued without loss of "
    "working context. Preserve VERBATIM every: decision made, file path, "
    "identifier/name, number, and unresolved question. Be concise but do not "
    "drop specifics — a later turn must be able to act on this summary alone:\n\n"
)

# Per-message clip when assembling compression input. 400 was too aggressive —
# it truncated code/paths mid-line before the summariser ever saw them (T-150).
_CTX_CLIP = 1200

# Summary token budget scales with how much is being compressed, instead of a
# flat 300 that crushed a whole session into a few lines (T-150).
_SUMMARY_TOKENS_PER_MSG = 60
_SUMMARY_TOKENS_MIN = 300
_SUMMARY_TOKENS_MAX = 1024


def _summary_budget(n_messages: int) -> int:
    """Token budget for the compression summary, scaled to input size."""
    return max(_SUMMARY_TOKENS_MIN,
               min(_SUMMARY_TOKENS_MAX, _SUMMARY_TOKENS_PER_MSG * n_messages))


def _block_text(block: Any) -> Optional[str]:
    """Return the readable text of one content block, or None if it carries none.

    Three shapes occur in self.messages (T-148):
      - Anthropic SDK objects with a `.text` attribute (legacy / direct-client path)
      - canonical dicts {"type": "text", "text": ...}  ← what _build_assistant_content
        stores for EVERY assistant turn, and the shape that the old
        `hasattr(block, "text")` check silently dropped
      - tool_result dicts {"type": "tool_result", "content": ...}
    """
    if hasattr(block, "text"):                       # SDK object
        return block.text
    if isinstance(block, dict):
        if block.get("type") == "text":              # canonical assistant text
            return block.get("text", "")
        if block.get("type") == "tool_result":
            return f"[tool_result] {str(block.get('content', ''))[:200]}"
    return None


def _build_context(messages: List[Dict]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, str):
            lines.append(f"{role}: {content[:_CTX_CLIP]}")
        elif isinstance(content, list):
            for block in content:
                text = _block_text(block)
                if text:
                    lines.append(f"{role}: {text[:_CTX_CLIP]}")
    return "\n".join(lines)


def compress_messages_with_groq(
    messages: List[Dict],
    groq_client,
    threshold: int = 30,
    keep_recent: int = 12,
    anthropic_client: Any = None,
) -> List[Dict]:
    """Compress old messages into a summary when history grows large.

    Provider chain (T-092): Groq llama-3.3-70b → Claude Haiku 4.5 → hard truncation.

    When len(messages) >= threshold, the oldest (len - keep_recent) messages
    are summarised into a single synthetic user message; only keep_recent
    most-recent messages are kept. Returns the original list unchanged if both
    LLMs fail, so the caller is never left with an empty history.

    Args:
        messages:          The full message list.
        groq_client:       Initialised groq.Groq instance.
        threshold:         Minimum list length before compression runs.
        keep_recent:       How many recent messages to preserve verbatim.
        anthropic_client:  anthropic.Anthropic instance for Haiku fallback (optional).
    """
    if len(messages) < threshold:
        return list(messages)

    to_compress = messages[:-keep_recent]
    recent = messages[-keep_recent:]
    context = _build_context(to_compress)

    if not context.strip():
        return list(messages)

    summary: Optional[str] = None
    budget = _summary_budget(len(to_compress))  # T-150: scale with input size

    # 1. Try Groq (free, primary)
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": _COMPRESS_PROMPT + context}],
            max_tokens=budget,
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as groq_err:
        print(f"[Pi] history compression: Groq failed ({groq_err}), trying Haiku")

    # 2. Fallback to Claude Haiku 4.5
    if summary is None and anthropic_client is not None:
        try:
            resp = anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=budget,
                messages=[{"role": "user", "content": _COMPRESS_PROMPT + context}],
            )
            summary = resp.content[0].text.strip() if resp.content else None
        except Exception as haiku_err:
            print(f"[Pi] history compression: both LLMs failed ({haiku_err}), hard-truncating")

    if summary is None:
        raise CompressionFailed(list(messages))

    summary_msg = {
        "role": "user",
        "content": f"[CONVERSATION SUMMARY — earlier context compressed]\n{summary}",
    }
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
                text = _block_text(block)
                if text:
                    lines.append(f"{role}: {text[:300]}")
    return "\n".join(lines)
