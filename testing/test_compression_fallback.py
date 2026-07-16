"""T-092 / T-108 — History compression Haiku fallback tests."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch
import pytest
from agent.truncation import compress_messages_with_groq, truncate_messages_safely, CompressionFailed


def _make_messages(n: int):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"} for i in range(n)]


class _FakeMsg:
    content = "• point one\n• point two\n• point three"

class _FakeChoice:
    message = _FakeMsg()

class FakeGroqResp:
    choices = [_FakeChoice()]


class FakeHaikuContent:
    text = "• haiku point one\n• haiku point two"


class FakeHaikuResp:
    content = [FakeHaikuContent()]


def test_groq_success_no_haiku_call():
    groq = MagicMock()
    groq.chat.completions.create.return_value = FakeGroqResp()
    anthropic = MagicMock()

    msgs = _make_messages(32)
    result = compress_messages_with_groq(msgs, groq, threshold=30, keep_recent=12, anthropic_client=anthropic)

    assert len(result) <= 14
    assert result[0]["content"].startswith("[CONVERSATION DIGEST")
    anthropic.messages.create.assert_not_called()


def test_groq_rate_limit_falls_back_to_haiku():
    from groq import RateLimitError
    groq = MagicMock()
    groq.chat.completions.create.side_effect = RateLimitError(
        message="rate limited", response=MagicMock(status_code=429, headers={}), body={}
    )
    anthropic = MagicMock()
    anthropic.messages.create.return_value = FakeHaikuResp()

    msgs = _make_messages(32)
    result = compress_messages_with_groq(msgs, groq, threshold=30, keep_recent=12, anthropic_client=anthropic)

    anthropic.messages.create.assert_called_once()
    call_kwargs = anthropic.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert result[0]["content"].startswith("[CONVERSATION DIGEST")
    assert "haiku" in result[0]["content"]


def test_groq_generic_error_falls_back_to_haiku():
    groq = MagicMock()
    groq.chat.completions.create.side_effect = Exception("connection reset")
    anthropic = MagicMock()
    anthropic.messages.create.return_value = FakeHaikuResp()

    msgs = _make_messages(32)
    result = compress_messages_with_groq(msgs, groq, threshold=30, keep_recent=12, anthropic_client=anthropic)

    anthropic.messages.create.assert_called_once()
    assert result[0]["content"].startswith("[CONVERSATION DIGEST")


def test_both_llms_fail_raises_compression_failed():
    """T-108: both providers fail → CompressionFailed carries original messages."""
    groq = MagicMock()
    groq.chat.completions.create.side_effect = Exception("groq down")
    anthropic = MagicMock()
    anthropic.messages.create.side_effect = Exception("haiku down")

    msgs = _make_messages(32)
    with pytest.raises(CompressionFailed) as exc_info:
        compress_messages_with_groq(msgs, groq, threshold=30, keep_recent=12, anthropic_client=anthropic)

    assert exc_info.value.original_messages == msgs


def test_both_llms_fail_no_anthropic_client_raises():
    """T-108: Groq fails, no Haiku client → CompressionFailed."""
    groq = MagicMock()
    groq.chat.completions.create.side_effect = Exception("groq down")

    msgs = _make_messages(32)
    with pytest.raises(CompressionFailed) as exc_info:
        compress_messages_with_groq(msgs, groq, threshold=30, keep_recent=12, anthropic_client=None)

    assert exc_info.value.original_messages == msgs


def test_below_threshold_no_compression():
    groq = MagicMock()
    msgs = _make_messages(20)
    result = compress_messages_with_groq(msgs, groq, threshold=30, keep_recent=12)
    assert result == msgs
    groq.chat.completions.create.assert_not_called()


# ── T-150: compression fidelity (budget scaling + clip) ──────────────────────

def test_summary_budget_scales_with_input():
    from agent.truncation import _summary_budget
    assert _summary_budget(1) == 300       # floor for tiny input
    assert _summary_budget(8) == 480       # 60 * 8, mid-range
    assert _summary_budget(20) == 1024     # 60*20=1200, capped


def test_compression_requests_more_than_flat_300():
    """A large conversation must get a bigger summary budget, not a flat 300."""
    groq = MagicMock()
    groq.chat.completions.create.return_value = FakeGroqResp()
    compress_messages_with_groq(_make_messages(40), groq, threshold=30, keep_recent=12)
    assert groq.chat.completions.create.call_args[1]["max_tokens"] > 300


def test_build_context_preserves_long_line():
    """400-char clip used to cut file paths / code mid-line before summarising."""
    from agent.truncation import _build_context
    longline = "edited src/" + ("a" * 600) + "/module.py:1234 — fixed the bug"
    ctx = _build_context([{"role": "user", "content": longline}])
    assert longline in ctx
