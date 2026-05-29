"""core/providers/anthropic.py — Anthropic/Claude provider adapter (T-048, T-061).

T-061: Prompt caching support. When system is a (static, dynamic) tuple:
  - static block gets cache_control: ephemeral → served from cache on subsequent turns
  - last tool schema also gets cache_control: ephemeral
  This cuts TTFT dramatically on cache hits and reduces cost ~90% on cached tokens.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Union

from core.llm_router import LLMResponse, ToolCall


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def chat(
        self,
        messages: List[Dict],
        system: Union[str, Tuple] = "",
        tools: List[Dict] = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        # Build system blocks — tuple triggers prompt caching.
        # 2-tuple: (static, dynamic)  — one cache point (T-061)
        # 3-tuple: (static, warm, dynamic) — two cache points (T-091)
        if isinstance(system, tuple) and len(system) >= 2:
            if len(system) == 3:
                static, warm, dynamic = system
            else:
                static, dynamic = system
                warm = ""
            system_param: List[Dict] = []
            if static:
                system_param.append({
                    "type": "text",
                    "text": static,
                    "cache_control": {"type": "ephemeral"},
                })
            if warm:
                system_param.append({
                    "type": "text",
                    "text": warm,
                    "cache_control": {"type": "ephemeral"},
                })
            if dynamic:
                system_param.append({"type": "text", "text": dynamic})
        else:
            system_param = [{"type": "text", "text": system}] if system else []

        kwargs: Dict = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=messages,
        )
        if system_param:
            kwargs["system"] = system_param

        # Add cache_control to last tool so the full schema is cached alongside the system
        if tools:
            tools_cached = list(tools)
            last = dict(tools_cached[-1])
            last["cache_control"] = {"type": "ephemeral"}
            tools_cached[-1] = last
            kwargs["tools"] = tools_cached

        resp = self._client.messages.create(**kwargs)

        text = "".join(
            (b.text if hasattr(b, "text") else b.get("text", ""))
            for b in resp.content
            if (getattr(b, "type", None) or b.get("type", "")) == "text"
        )
        tool_calls = [
            ToolCall(
                id=b.id if hasattr(b, "id") else b.get("id", ""),
                name=b.name if hasattr(b, "name") else b.get("name", ""),
                input=dict(b.input if hasattr(b, "input") else b.get("input", {})),
            )
            for b in resp.content
            if (getattr(b, "type", None) or (b.get("type", "") if isinstance(b, dict) else "")) == "tool_use"
        ]

        stop = "tool_use" if resp.stop_reason == "tool_use" else "end_turn"
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            tool_calls=tool_calls,
            stop_reason=stop,
            tokens_in=resp.usage.input_tokens if resp.usage else 0,
            tokens_out=resp.usage.output_tokens if resp.usage else 0,
        )

    def ping(self) -> None:
        """Lightweight liveness check — consumes minimal tokens."""
        self._client.messages.create(
            model=self.model,
            max_tokens=1,
            system="ping",
            messages=[{"role": "user", "content": "ping"}],
        )
