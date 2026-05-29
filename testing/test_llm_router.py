"""Tests for core/llm_router.py + providers + schema_translate (T-048/T-049)."""
from __future__ import annotations

import sys
import types
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_anthropic_response(text="hello", tool_calls=None):
    resp = MagicMock()
    resp.stop_reason = "tool_use" if tool_calls else "end_turn"
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5

    blocks = []
    if text:
        tb = MagicMock()
        tb.type = "text"
        tb.text = text
        blocks.append(tb)

    for tc in (tool_calls or []):
        ub = MagicMock()
        ub.type = "tool_use"
        ub.id = tc["id"]
        ub.name = tc["name"]
        ub.input = tc["input"]
        blocks.append(ub)

    resp.content = blocks
    return resp


# ── LLMResponse / ToolCall dataclasses ───────────────────────────────────────

class TestDataclasses:
    def test_llm_response_defaults(self):
        from core.llm_router import LLMResponse
        r = LLMResponse(text="hi", provider="anthropic", model="claude-sonnet-4-6")
        assert r.stop_reason == "end_turn"
        assert r.tool_calls == []
        assert r.tokens_in == 0

    def test_tool_call(self):
        from core.llm_router import ToolCall
        tc = ToolCall(id="tc_1", name="memory_read", input={"query": "test"})
        assert tc.name == "memory_read"
        assert tc.input["query"] == "test"


# ── AnthropicProvider ─────────────────────────────────────────────────────────

class TestAnthropicProvider:

    def test_chat_text_only(self):
        from core.providers.anthropic import AnthropicProvider

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response("hello")

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider._client = mock_client
        provider.model = "claude-sonnet-4-6"

        resp = provider.chat([{"role": "user", "content": "hi"}], "sys", [], 100)
        assert resp.text == "hello"
        assert resp.stop_reason == "end_turn"
        assert resp.tool_calls == []
        assert resp.provider == "anthropic"

    def test_chat_tool_use(self):
        from core.providers.anthropic import AnthropicProvider

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(
            text="",
            tool_calls=[{"id": "tu_1", "name": "memory_read", "input": {"query": "test"}}],
        )

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider._client = mock_client
        provider.model = "claude-sonnet-4-6"

        resp = provider.chat([{"role": "user", "content": "recall test"}], "sys", [{"name": "memory_read"}], 100)
        assert resp.stop_reason == "tool_use"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "memory_read"
        assert resp.tool_calls[0].id == "tu_1"

    def test_token_counts(self):
        from core.providers.anthropic import AnthropicProvider

        mock_client = MagicMock()
        mock_resp = _mock_anthropic_response("hi")
        mock_resp.usage.input_tokens = 42
        mock_resp.usage.output_tokens = 7
        mock_client.messages.create.return_value = mock_resp

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider._client = mock_client
        provider.model = "claude-sonnet-4-6"

        resp = provider.chat([], "sys", [], 100)
        assert resp.tokens_in == 42
        assert resp.tokens_out == 7


# ── schema_translate ──────────────────────────────────────────────────────────

class TestSchemaTranslate:

    def test_anthropic_to_openai(self):
        from core.schema_translate import anthropic_to_openai_tools
        tools = [{
            "name": "memory_read",
            "description": "Read memory",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        }]
        out = anthropic_to_openai_tools(tools)
        assert out[0]["type"] == "function"
        assert out[0]["function"]["name"] == "memory_read"
        assert "query" in out[0]["function"]["parameters"]["properties"]

    def test_openai_tool_calls_to_unified(self):
        from core.schema_translate import openai_tool_calls_to_unified
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "memory_read"
        tc.function.arguments = '{"query": "test"}'

        result = openai_tool_calls_to_unified([tc])
        assert result[0].name == "memory_read"
        assert result[0].input["query"] == "test"

    def test_anthropic_messages_to_openai_simple(self):
        from core.schema_translate import anthropic_messages_to_openai
        msgs = [{"role": "user", "content": "hello"}]
        out = anthropic_messages_to_openai(msgs, "system prompt")
        assert out[0] == {"role": "system", "content": "system prompt"}
        assert out[1] == {"role": "user", "content": "hello"}

    def test_anthropic_messages_to_openai_tool_result(self):
        from core.schema_translate import anthropic_messages_to_openai
        msgs = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "checking..."},
                {"type": "tool_use", "id": "tu_1", "name": "memory_read", "input": {"query": "x"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"result": "y"}'},
            ]},
        ]
        out = anthropic_messages_to_openai(msgs, "sys")
        # assistant message should have tool_calls
        asst = next(m for m in out if m["role"] == "assistant")
        assert "tool_calls" in asst
        assert asst["tool_calls"][0]["function"]["name"] == "memory_read"
        # tool result should be a "tool" role message
        tool_msg = next(m for m in out if m["role"] == "tool")
        assert tool_msg["tool_call_id"] == "tu_1"


# ── LLMRouter ─────────────────────────────────────────────────────────────────

class TestLLMRouter:

    def _make_router(self):
        from core.llm_router import LLMRouter, LLMResponse
        from core.providers.anthropic import AnthropicProvider

        mock_provider = MagicMock()
        mock_provider.name = "anthropic"
        mock_provider.chat.return_value = LLMResponse(
            text="ok", provider="anthropic", model="claude-sonnet-4-6"
        )

        router = LLMRouter.__new__(LLMRouter)
        router._providers = [mock_provider]
        router._brownout = {}
        return router, mock_provider

    def test_routes_to_first_provider(self):
        router, mock_provider = self._make_router()
        resp = router.chat([{"role": "user", "content": "hi"}], "sys")
        assert resp.text == "ok"
        mock_provider.chat.assert_called_once()

    def test_brownout_skips_provider(self):
        import time
        from core.llm_router import LLMRouter, LLMResponse

        p1 = MagicMock()
        p1.name = "anthropic"
        p2 = MagicMock()
        p2.name = "groq"
        p2.chat.return_value = LLMResponse(text="groq fallback", provider="groq", model="llama")

        router = LLMRouter.__new__(LLMRouter)
        router._providers = [p1, p2]
        router._brownout = {"anthropic": time.time()}  # mark anthropic as browned out

        resp = router.chat([])
        assert resp.text == "groq fallback"
        p1.chat.assert_not_called()

    def test_marks_brownout_on_failure(self):
        from core.llm_router import LLMRouter, LLMResponse

        p1 = MagicMock()
        p1.name = "anthropic"
        p1.chat.side_effect = RuntimeError("API down")
        p2 = MagicMock()
        p2.name = "groq"
        p2.chat.return_value = LLMResponse(text="fallback", provider="groq", model="llama")

        router = LLMRouter.__new__(LLMRouter)
        router._providers = [p1, p2]
        router._brownout = {}

        resp = router.chat([])
        assert resp.text == "fallback"
        assert "anthropic" in router._brownout

    def test_all_providers_down_raises(self):
        from core.llm_router import LLMRouter

        p1 = MagicMock()
        p1.name = "anthropic"
        p1.chat.side_effect = RuntimeError("down")

        router = LLMRouter.__new__(LLMRouter)
        router._providers = [p1]
        router._brownout = {}

        with pytest.raises(RuntimeError, match="All LLM providers"):
            router.chat([])

    def test_health_clears_brownout(self):
        import time
        from core.llm_router import LLMRouter

        p1 = MagicMock()
        p1.name = "anthropic"
        p1.ping.return_value = None  # ping succeeds

        router = LLMRouter.__new__(LLMRouter)
        router._providers = [p1]
        router._brownout = {"anthropic": time.time()}  # was browned out

        health = router.health()
        assert health["anthropic"] is True
        assert "anthropic" not in router._brownout  # cleared


# ── T-115: generation error classifier + no-brownout + no-tools retry ─────────

class TestGenerationErrorClassifier:
    def test_tool_use_failed_is_generation_error(self):
        from core.llm_router import LLMRouter
        exc = RuntimeError("BadRequestError: 400 tool_use_failed")
        assert LLMRouter._is_generation_error(exc)

    def test_context_length_exceeded_is_generation_error(self):
        from core.llm_router import LLMRouter
        exc = RuntimeError("context_length_exceeded: max 8192 tokens")
        assert LLMRouter._is_generation_error(exc)

    def test_rate_limit_429_is_not_generation_error(self):
        from core.llm_router import LLMRouter
        exc = RuntimeError("429 RateLimitError: too many requests")
        assert not LLMRouter._is_generation_error(exc)

    def test_server_error_500_is_not_generation_error(self):
        from core.llm_router import LLMRouter
        exc = RuntimeError("500 internal server error")
        assert not LLMRouter._is_generation_error(exc)

    def test_invalid_api_key_400_is_not_generation_error(self):
        from core.llm_router import LLMRouter
        exc = RuntimeError("400 invalid_api_key")
        assert not LLMRouter._is_generation_error(exc)

    def test_status_code_400_tool_use_failed(self):
        from core.llm_router import LLMRouter
        exc = Exception("tool_use_failed bad generation")
        exc.status_code = 400
        assert LLMRouter._is_generation_error(exc)


class TestNoBrownoutOnGenerationError:
    def _make_router(self, provider_name="groq"):
        from core.llm_router import LLMRouter, LLMResponse
        p = MagicMock()
        p.name = provider_name
        router = LLMRouter.__new__(LLMRouter)
        router._providers = [p]
        router._brownout = {}
        return router, p

    def test_tool_use_failed_does_not_brownout(self):
        from core.llm_router import LLMRouter, LLMResponse
        router, p = self._make_router()
        p.chat.side_effect = RuntimeError("400 tool_use_failed bad gen")

        with pytest.raises(RuntimeError):
            router.chat([{"role": "user", "content": "hi"}], "sys", tools=[{"name": "t"}])

        assert "groq" not in router._brownout, "tool_use_failed must not brownout the provider"

    def test_rate_limit_still_brownouts(self):
        from core.llm_router import LLMRouter, LLMResponse
        router, p = self._make_router()
        p.chat.side_effect = RuntimeError("429 RateLimitError")

        with pytest.raises(RuntimeError):
            router.chat([{"role": "user", "content": "hi"}], "sys")

        assert "groq" in router._brownout, "rate limit must still brownout the provider"

    def test_tool_use_failed_retries_without_tools(self):
        from core.llm_router import LLMRouter, LLMResponse

        p1 = MagicMock()
        p1.name = "groq"
        # First call (with tools) fails with tool_use_failed
        # Second call (no tools) succeeds
        success_resp = LLMResponse(text="plain answer", provider="groq", model="llama")
        p1.chat.side_effect = [RuntimeError("400 tool_use_failed"), success_resp]

        router = LLMRouter.__new__(LLMRouter)
        router._providers = [p1]
        router._brownout = {}

        tools = [{"name": "search", "description": "search tool"}]
        resp = router.chat([{"role": "user", "content": "search for X"}], "sys", tools=tools)

        assert resp.text == "plain answer"
        assert p1.chat.call_count == 2
        # Second call must have been made with empty tools list
        second_call_tools = p1.chat.call_args[0][2]  # positional arg index 2
        assert second_call_tools == []
        assert "groq" not in router._brownout
