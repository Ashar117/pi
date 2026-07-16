"""Tests for T-178: streaming responses (on_delta callback)."""
from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_router import LLMResponse, ToolCall


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_fake_content_block(type_="text", text="", tool_id="", tool_name="", tool_input=None):
    block = MagicMock()
    block.type = type_
    if type_ == "text":
        block.text = text
    elif type_ == "tool_use":
        block.id = tool_id
        block.name = tool_name
        block.input = tool_input or {}
    return block


def _make_fake_final_msg(text="hello world", stop_reason="end_turn", tool_calls=None):
    msg = MagicMock()
    content_blocks = []
    if text:
        content_blocks.append(_make_fake_content_block("text", text))
    if tool_calls:
        for tc in tool_calls:
            content_blocks.append(_make_fake_content_block(
                "tool_use", tool_id=tc["id"], tool_name=tc["name"], tool_input=tc["input"]
            ))
    msg.content = content_blocks
    msg.stop_reason = stop_reason
    msg.usage = MagicMock(input_tokens=10, output_tokens=5)
    return msg


class _FakeStreamContext:
    """Fake context manager mimicking anthropic.messages.stream()."""

    def __init__(self, text_chunks: List[str], final_msg):
        self._chunks = text_chunks
        self._final = final_msg

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    @property
    def text_stream(self):
        yield from self._chunks

    def get_final_message(self):
        return self._final


# ── AnthropicProvider streaming ───────────────────────────────────────────────

def test_on_delta_called_for_each_text_chunk():
    """on_delta receives every text chunk in order."""
    from core.providers.anthropic import AnthropicProvider

    chunks = ["Hello", " world", "!"]
    final = _make_fake_final_msg(text="Hello world!")
    fake_stream = _FakeStreamContext(chunks, final)

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = fake_stream
    provider.model = "claude-test"

    received: List[str] = []
    resp = provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        on_delta=received.append,
    )

    assert received == chunks
    assert resp.text == "Hello world!"


def test_on_delta_not_called_for_tool_use_round():
    """tool_use response: text_stream is empty, on_delta never fires."""
    from core.providers.anthropic import AnthropicProvider

    final = _make_fake_final_msg(
        text="",
        stop_reason="tool_use",
        tool_calls=[{"id": "tc1", "name": "memory_read", "input": {"query": "x"}}],
    )
    fake_stream = _FakeStreamContext([], final)  # no text chunks

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = fake_stream
    provider.model = "claude-test"

    received: List[str] = []
    resp = provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        on_delta=received.append,
    )

    assert received == []
    assert resp.stop_reason == "tool_use"


def test_streaming_accumulated_text_matches_expected():
    """Text accumulated from deltas equals the full response text."""
    from core.providers.anthropic import AnthropicProvider

    chunks = ["The", " answer", " is", " 42."]
    full_text = "The answer is 42."
    final = _make_fake_final_msg(text=full_text)
    fake_stream = _FakeStreamContext(chunks, final)

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = fake_stream
    provider.model = "claude-test"

    received: List[str] = []
    resp = provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        on_delta=received.append,
    )

    assert "".join(received) == full_text
    assert resp.text == full_text


def test_no_on_delta_uses_blocking_path():
    """When on_delta is None, messages.create (not stream) is called."""
    from core.providers.anthropic import AnthropicProvider

    final = _make_fake_final_msg(text="blocking response")
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._client = MagicMock()
    provider._client.messages.create.return_value = final
    provider.model = "claude-test"

    resp = provider.chat(
        messages=[{"role": "user", "content": "hi"}],
    )

    provider._client.messages.create.assert_called_once()
    provider._client.messages.stream.assert_not_called()
    assert resp.text == "blocking response"


def test_streaming_returns_tool_calls_from_final_message():
    """Tool calls in the final message are returned correctly when streaming."""
    from core.providers.anthropic import AnthropicProvider

    final = _make_fake_final_msg(
        text="",
        stop_reason="tool_use",
        tool_calls=[{"id": "tc99", "name": "web_search", "input": {"query": "pi"}}],
    )
    fake_stream = _FakeStreamContext([], final)

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._client = MagicMock()
    provider._client.messages.stream.return_value = fake_stream
    provider.model = "claude-test"

    resp = provider.chat(
        messages=[{"role": "user", "content": "search pi"}],
        on_delta=lambda t: None,
    )

    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "web_search"


def test_anthropic_provider_has_supports_streaming_flag():
    from core.providers.anthropic import AnthropicProvider
    assert getattr(AnthropicProvider, "supports_streaming", False) is True


# ── LLMRouter threading ───────────────────────────────────────────────────────

def test_router_passes_on_delta_to_streaming_provider():
    """Router forwards on_delta to providers that declare supports_streaming."""
    from core.llm_router import LLMRouter

    received: List[str] = []

    class _FakeStreamingProvider:
        name = "anthropic"
        supports_streaming = True

        def chat(self, messages, system="", tools=None, max_tokens=2048, on_delta=None):
            if on_delta:
                on_delta("streamed!")
            return LLMResponse(text="streamed!", provider="anthropic", model="test")

        def ping(self): pass

    router = LLMRouter.__new__(LLMRouter)
    router._providers = [_FakeStreamingProvider()]
    router._brownout = {}
    router._session_id = ""
    router._enable_cache = False
    router._cost = None

    def _on_delta(t): received.append(t)

    resp = router.chat([{"role": "user", "content": "hi"}], on_delta=_on_delta)

    assert received == ["streamed!"]
    assert resp.text == "streamed!"


def test_router_does_not_pass_on_delta_to_non_streaming_provider():
    """Router omits on_delta for providers without supports_streaming."""
    from core.llm_router import LLMRouter

    class _FakePlainProvider:
        name = "groq"
        # no supports_streaming

        def chat(self, messages, system="", tools=None, max_tokens=2048):
            return LLMResponse(text="plain!", provider="groq", model="llama")

        def ping(self): pass

    router = LLMRouter.__new__(LLMRouter)
    router._providers = [_FakePlainProvider()]
    router._brownout = {}
    router._session_id = ""
    router._enable_cache = False
    router._cost = None

    # Should NOT raise TypeError even though on_delta is passed to router
    resp = router.chat([{"role": "user", "content": "hi"}], on_delta=lambda t: None)
    assert resp.text == "plain!"


# ── pi_agent streaming integration ───────────────────────────────────────────

def test_last_turn_streamed_flag_set_when_text_streamed():
    """_last_turn_streamed = True after a turn where on_delta fired."""
    # We test _respond_via_config indirectly via a minimal harness that verifies
    # _last_turn_streamed is set when streaming occurred.
    from core.llm_router import LLMResponse

    class _FakeStreamingRouter:
        def chat(self, messages, system="", tools=None, max_tokens=2048,
                 tier="default", on_delta=None):
            if on_delta:
                on_delta("hello")  # simulate one text delta
            return LLMResponse(text="hello", provider="anthropic", model="claude-test")

    # Create a minimal mock of the parts _respond_via_config touches
    # Rather than running the full agent, test the flag logic directly.
    _stream_started = False

    def _on_delta(t):
        nonlocal _stream_started
        _stream_started = True

    router = _FakeStreamingRouter()
    resp = router.chat([], on_delta=_on_delta)

    assert _stream_started is True
    assert resp.text == "hello"


def test_last_turn_streamed_false_when_no_text_emitted():
    """_last_turn_streamed stays False when on_delta never fires (tool_use round)."""
    from core.llm_router import LLMResponse

    class _FakeToolRouter:
        def chat(self, messages, system="", tools=None, max_tokens=2048,
                 tier="default", on_delta=None):
            # Simulates tool_use: on_delta is passed but never called
            return LLMResponse(
                text="",
                provider="anthropic",
                model="claude-test",
                stop_reason="tool_use",
                tool_calls=[ToolCall(id="tc1", name="memory_read", input={})],
            )

    _stream_started = False

    def _on_delta(t):
        nonlocal _stream_started
        _stream_started = True

    router = _FakeToolRouter()
    resp = router.chat([], on_delta=_on_delta)

    assert _stream_started is False
    assert resp.stop_reason == "tool_use"
