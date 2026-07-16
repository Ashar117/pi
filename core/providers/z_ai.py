"""core/providers/z_ai.py — Z.AI provider (OpenAI-compatible).

Base URL: https://api.z.ai/api/paas/v4
Free models: glm-4.7-flash, glm-4.5-flash (no payment method required)
Paid models: glm-5.2 ($1.4/$4.4 per 1M tokens), glm-5.1, glm-4.7, etc.

API is a drop-in OpenAI-compatible endpoint; uses openai SDK with custom base_url.
"""
from __future__ import annotations

from typing import Dict, List

from core.llm_router import LLMResponse, ToolCall
from core.schema_translate import (
    anthropic_messages_to_openai,
    anthropic_to_openai_tools,
    openai_tool_calls_to_unified,
)

_BASE_URL = "https://api.z.ai/api/paas/v4"


class ZAIProvider:
    name = "z_ai"

    def __init__(
        self,
        api_key: str,
        model: str = "glm-4.7-flash",
    ):
        from openai import OpenAI
        self._client = OpenAI(
            api_key=api_key,
            base_url=_BASE_URL,
            timeout=30.0,
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
