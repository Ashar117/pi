"""testing/test_conversation_schema.py — T-161: canonical Turn schema + single extractor.

Verifies that:
- agent.conversation.Turn is the canonical shape (TypedDict with required fields)
- message_text() handles both content shapes without inline isinstance() scattered elsewhere
- _block_text is re-exported as the canonical block extractor
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.conversation import Turn, message_text, _block_text


# ── 1. Turn TypedDict fields ───────────────────────────────────────────────────

def test_turn_has_required_fields():
    hints = Turn.__annotations__
    for field in ("role", "content"):
        assert field in hints, f"Turn must have {field!r} field"


def test_turn_has_metadata_fields():
    hints = Turn.__annotations__
    for field in ("mode", "conversation_id", "ts"):
        assert field in hints, f"Turn must have metadata field {field!r}"


def test_turn_can_be_constructed_as_dict():
    t: Turn = {"role": "user", "content": "hello"}
    assert t["role"] == "user"
    assert t["content"] == "hello"


# ── 2. message_text() handles both content shapes ────────────────────────────

def test_message_text_plain_string():
    msg = {"role": "user", "content": "hello world"}
    assert message_text(msg) == "hello world"


def test_message_text_block_list():
    msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "the answer is"},
            {"type": "text", "text": "42"},
        ],
    }
    result = message_text(msg)
    assert "the answer is" in result
    assert "42" in result


def test_message_text_skips_tool_use_blocks():
    msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "using a tool"},
            {"type": "tool_use", "id": "t1", "name": "web_search", "input": {}},
        ],
    }
    result = message_text(msg)
    assert "using a tool" in result
    assert "tool_use" not in result
    assert "web_search" not in result


def test_message_text_empty_returns_empty_string():
    assert message_text({}) == ""
    assert message_text({"role": "assistant", "content": []}) == ""
    assert message_text({"role": "assistant", "content": ""}) == ""


def test_message_text_never_returns_none():
    """The function contract: never returns None."""
    for msg in [{}, {"content": None}, {"content": []}, {"content": ""}]:
        result = message_text(msg)
        assert result is not None, f"message_text({msg!r}) returned None"
        assert isinstance(result, str)


# ── 3. _block_text is re-exported as canonical extractor ─────────────────────

def test_block_text_reexported():
    """_block_text must be importable from agent.conversation (re-export)."""
    assert callable(_block_text), "_block_text must be callable"


def test_block_text_handles_text_block():
    block = {"type": "text", "text": "hello"}
    assert _block_text(block) == "hello"
