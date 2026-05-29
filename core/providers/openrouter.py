"""core/providers/openrouter.py — OpenRouter provider (OpenAI-compatible).

OpenRouter routes to 100+ models. Several are free (marked :free suffix).
Sign up at openrouter.ai — free credits on signup.

Free models (as of 2026):
  meta-llama/llama-3.3-70b-instruct:free
  google/gemma-3-27b-it:free
  mistralai/mistral-7b-instruct:free
  deepseek/deepseek-r1:free
"""
from __future__ import annotations

from typing import Dict, List

from core.llm_router import LLMResponse, ToolCall
from core.schema_translate import (
    anthropic_messages_to_openai,
    anthropic_to_openai_tools,
    openai_tool_calls_to_unified,
)

_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider:
    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: str = "meta-llama/llama-3.3-70b-instruct:free",
    ):
        from openai import OpenAI
        self._client = OpenAI(
            api_key=api_key,
            base_url=_BASE_URL,
            default_headers={"X-Title": "Pi-Agent"},
        )
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
