"""core/providers/qwen.py — Qwen provider via Alibaba Cloud Model Studio (DashScope).

Base URL: https://dashscope-intl.aliyuncs.com/compatible-mode/v1
Models: qwen3.7-max (flagship reasoning, confirmed live 2026-07-19), qwen-plus (balanced), qwen-turbo (fast/cheap).

This file is also the Alibaba Cloud proof-of-use for the Qwen Cloud hackathon
submission: all calls go to Alibaba Cloud's DashScope OpenAI-compatible endpoint.
"""
from __future__ import annotations

from typing import Dict, List

from core.llm_router import LLMResponse, ToolCall
from core.schema_translate import (
    anthropic_messages_to_openai,
    anthropic_to_openai_tools,
    openai_tool_calls_to_unified,
)

_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


class QwenProvider:
    name = "qwen"

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3.7-max",
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
