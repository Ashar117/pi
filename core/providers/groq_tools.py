"""core/providers/groq_tools.py — Groq provider with tool-calling support (T-048).

Groq uses OpenAI-compatible API. Messages are translated from Anthropic canonical
format to OpenAI format before the call. Tool calls are mapped back to ToolCall.
"""
from __future__ import annotations

from typing import Dict, List

from core.llm_router import LLMResponse, ToolCall
from core.schema_translate import (
    anthropic_messages_to_openai,
    anthropic_to_openai_tools,
    openai_tool_calls_to_unified,
)


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        from groq import Groq
        self._client = Groq(api_key=api_key, timeout=25.0)  # T-237: hard timeout so hung call doesn't block
        self.model = model

    def chat(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int = 2048,
    ) -> LLMResponse:
        openai_msgs = anthropic_messages_to_openai(messages, system)
        kwargs: Dict = dict(
            model=self.model,
            messages=openai_msgs,
            max_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"] = anthropic_to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"

        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        text = msg.content or ""
        tool_calls = openai_tool_calls_to_unified(
            getattr(msg, "tool_calls", None) or []
        )
        stop = "tool_use" if tool_calls else "end_turn"

        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            tool_calls=tool_calls,
            stop_reason=stop,
        )

    def ping(self) -> None:
        self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
