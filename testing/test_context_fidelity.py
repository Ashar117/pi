"""T-148 — Conversation context fidelity.

These tests guard the invariant the suite was missing: Pi's OWN replies must
survive the round-trip through the context extractors. They were silently
dropped because `_build_assistant_content` stores assistant turns as canonical
dicts ({"type": "text", "text": ...}) while the extractors only matched SDK
objects (`hasattr(block, "text")`). That is the root cause of T-143
(incoherent replies) and it also poisoned L2 distillation via
generate_session_summary.

Critically: messages here are built in the SAME shape pi_agent.py actually
stores — user turns as plain strings, assistant turns as dict-block lists.
The pre-existing test_compression_fallback.py used `"msg {i}"` strings only,
which is exactly why the bug shipped green.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.truncation import (
    extract_text_from_messages,
    _build_context,
    _block_text,
    truncate_messages_safely,
)


def _assistant(text: str) -> dict:
    """Mirror pi_agent._build_assistant_content for a text-only reply."""
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _real_conversation() -> list:
    return [
        _user("my deadline for the GNN paper is June 10"),
        _assistant("Got it — June 10 for the GNN paper. Want a reminder?"),
        _user("what did I just tell you my deadline was?"),
    ]


# ── _block_text: the new shared helper ──────────────────────────────────────

def test_block_text_canonical_assistant_dict():
    assert _block_text({"type": "text", "text": "hello"}) == "hello"


def test_block_text_tool_result_dict():
    out = _block_text({"type": "tool_result", "content": "42 rows"})
    assert out is not None and "42 rows" in out


def test_block_text_sdk_object_still_works():
    class _SDK:
        text = "sdk text"
    assert _block_text(_SDK()) == "sdk text"


def test_block_text_tool_use_returns_none():
    # tool_use blocks carry no readable text for context
    assert _block_text({"type": "tool_use", "id": "x", "name": "f", "input": {}}) is None


# ── extract_text_from_messages: normie session context + session summary ─────

def test_assistant_reply_survives_extraction():
    """The core regression: Pi must see what Pi said."""
    ctx = extract_text_from_messages(_real_conversation(), n=10)
    assert "June 10 for the GNN paper" in ctx, (
        "assistant reply was dropped from context — T-143 root cause"
    )


def test_both_sides_present_in_extraction():
    ctx = extract_text_from_messages(_real_conversation(), n=10)
    assert ctx.count("assistant:") >= 1
    assert ctx.count("user:") >= 2


def test_tool_result_surfaces_in_extraction():
    msgs = [
        _user("how many rows?"),
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "1234 rows"}
        ]},
    ]
    ctx = extract_text_from_messages(msgs, n=10)
    assert "1234 rows" in ctx


# ── _build_context: root-mode compression summarizer input ───────────────────

def test_build_context_includes_assistant_turns():
    ctx = _build_context(_real_conversation())
    assert "June 10 for the GNN paper" in ctx, (
        "compression summary input dropped assistant turns — corrupts the "
        "summary that replaces older history"
    )


# ── truncate_messages_safely: never wipe history ─────────────────────────────

def test_truncate_never_returns_empty_on_tool_heavy_tail():
    """A long tool-only tail used to return [] (total amnesia). Must not."""
    msgs = [_user("start the job")]
    # 30 alternating assistant(tool_use)/user(tool_result) blocks, no plain
    # string user message in the tail window.
    for i in range(30):
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "f", "input": {}}]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": str(i)}]})
    out = truncate_messages_safely(msgs, max_messages=10)
    assert len(out) > 0, "truncation wiped all history"


def test_truncate_keeps_recent_user_string_boundary():
    msgs = [_user(f"turn {i}") for i in range(40)]
    # interleave assistant dict replies
    full = []
    for m in msgs:
        full.append(m)
        full.append(_assistant("ok"))
    out = truncate_messages_safely(full, max_messages=10)
    assert len(out) > 0
    assert out[0]["role"] == "user" and isinstance(out[0]["content"], str)


def test_truncate_passthrough_when_short():
    msgs = _real_conversation()
    assert truncate_messages_safely(msgs, max_messages=20) == msgs
