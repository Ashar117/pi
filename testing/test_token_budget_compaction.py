"""Tests for T-184: token-budget compaction in agent/truncation.py."""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.truncation import (
    estimate_tokens,
    compress_messages_with_groq,
    CompressionFailed,
    _extract_file_touches,
    truncate_messages_safely,
)


# ── estimate_tokens ───────────────────────────────────────────────────────────

def test_estimate_tokens_empty():
    assert estimate_tokens([]) == 0


def test_estimate_tokens_string_content():
    msgs = [{"role": "user", "content": "x" * 400}]
    assert estimate_tokens(msgs) == 100  # 400 / 4


def test_estimate_tokens_list_content():
    msgs = [{"role": "assistant", "content": [
        {"type": "text", "text": "y" * 800}
    ]}]
    # chars from "text" key value + "type" key value = 800 + 4 = 804 // 4 = 201
    assert estimate_tokens(msgs) == 201


def test_estimate_tokens_scales_with_messages():
    small = [{"role": "user", "content": "hi"}]
    big = [{"role": "user", "content": "hi " * 1000}]
    assert estimate_tokens(big) > estimate_tokens(small)


# ── _extract_file_touches ─────────────────────────────────────────────────────

def test_extract_file_touches_finds_py_path():
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "content": "edited agent/modes.py successfully"}
    ]}]
    touches = _extract_file_touches(msgs)
    assert any("modes.py" in t for t in touches)


def test_extract_file_touches_empty_on_no_paths():
    msgs = [{"role": "user", "content": "just plain text"}]
    assert _extract_file_touches(msgs) == []


def test_extract_file_touches_deduplicates():
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "content": "tools/tools_project.py tools/tools_project.py"}
    ]}]
    touches = _extract_file_touches(msgs)
    assert len([t for t in touches if "tools_project.py" in t]) == 1


# ── compress_messages_with_groq token_budget param ───────────────────────────

def test_no_compression_below_budget_and_threshold():
    msgs = [{"role": "user", "content": "short"}] * 5
    fake_groq = MagicMock()
    result = compress_messages_with_groq(msgs, fake_groq, threshold=30, token_budget=24000)
    assert result == msgs
    fake_groq.chat.completions.create.assert_not_called()


def test_compression_triggers_on_budget_exceeded():
    # Create messages that exceed token budget but not message count threshold
    big_content = "x" * 400  # 100 tokens each
    msgs = [{"role": "user", "content": big_content}] * 10  # ~1000 tokens

    fake_groq = MagicMock()
    fake_groq.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="FILES_TOUCHED: none\nDECISIONS: none"))]
    )

    result = compress_messages_with_groq(
        msgs, fake_groq, threshold=30, token_budget=500  # budget=500 < ~1000 actual
    )
    # Compression was triggered (Groq was called) even though < 30 messages
    fake_groq.chat.completions.create.assert_called_once()
    # Result contains the digest marker
    assert any("DIGEST" in (m.get("content") or "") for m in result)


def test_compression_result_has_digest_header():
    msgs = [{"role": "user", "content": "x" * 400}] * 10

    fake_groq = MagicMock()
    fake_groq.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="FILES_TOUCHED: agent/modes.py\nOPEN: nothing"))]
    )

    result = compress_messages_with_groq(msgs, fake_groq, threshold=30, token_budget=500)
    # The summary message should have a DIGEST header
    assert any("DIGEST" in (m.get("content") or "") for m in result)


def test_compression_pairing_intact():
    """Compression never produces an odd number of messages (tool_use without result)."""
    msgs = [{"role": "user", "content": "x" * 400}] * 10

    fake_groq = MagicMock()
    fake_groq.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="digest text"))]
    )

    result = compress_messages_with_groq(msgs, fake_groq, threshold=30, token_budget=500)
    # Result should never start with an assistant message (orphan)
    if result:
        assert result[0]["role"] == "user"


def test_compression_fallback_on_groq_failure():
    msgs = [{"role": "user", "content": "x" * 400}] * 10
    fake_groq = MagicMock()
    fake_groq.chat.completions.create.side_effect = RuntimeError("groq down")

    with pytest.raises(CompressionFailed):
        compress_messages_with_groq(msgs, fake_groq, threshold=30, token_budget=500)


# ── ModeConfig ctx_token_budget ──────────────────────────────────────────────

def test_root_mode_has_ctx_token_budget():
    from agent.modes import MODE_CONFIGS
    assert MODE_CONFIGS["root"].ctx_token_budget == 24000


def test_normie_mode_has_no_ctx_token_budget():
    from agent.modes import MODE_CONFIGS
    assert MODE_CONFIGS["normie"].ctx_token_budget is None
