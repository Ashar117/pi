"""core/providers/gemini.py — Google Gemini provider adapter (T-048).

T-076: migrated from the deprecated ``google-generativeai`` package to the
current ``google-genai`` SDK. Public surface (``GeminiProvider.chat``,
``GeminiProvider.ping``) is unchanged; only the internal API calls differ.

Handles text generation and basic tool-calling via Gemini's function
declarations. Messages are translated from Anthropic canonical format.
"""
from __future__ import annotations

import uuid
from typing import Dict, List

from core.llm_router import LLMResponse, ToolCall
from core.schema_translate import anthropic_to_gemini_tools


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            btype = b.get("type") if isinstance(b, dict) else getattr(b, "type", "")
            if btype == "text":
                t = b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                parts.append(t)
        return " ".join(parts)
    return str(content)


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        from google import genai
        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self.model = model

    def chat(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int = 2048,
    ) -> LLMResponse:
        from google.genai import types

        gemini_tools = anthropic_to_gemini_tools(tools) if tools else None

        # Translate Anthropic messages into Gemini "contents" — list of
        # Content dicts with role "user"|"model" and a single text part.
        contents = []
        for m in messages:
            role = "user" if m.get("role") == "user" else "model"
            text = _extract_text(m.get("content", ""))
            if text:
                contents.append({"role": role, "parts": [{"text": text}]})
        if not contents:
            contents = [{"role": "user", "parts": [{"text": "ping"}]}]

        config_kwargs: Dict = {"max_output_tokens": max_tokens}
        if system:
            config_kwargs["system_instruction"] = system
        if gemini_tools:
            config_kwargs["tools"] = gemini_tools

        resp = self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        text_out = ""
        tool_calls: List[ToolCall] = []
        try:
            candidate = (resp.candidates or [None])[0]
            if candidate and candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if getattr(part, "text", None):
                        text_out += part.text
                    fc = getattr(part, "function_call", None)
                    if fc and getattr(fc, "name", None):
                        tool_calls.append(ToolCall(
                            id=f"gemini-{len(tool_calls)}-{uuid.uuid4().hex[:6]}",
                            name=fc.name,
                            input=dict(fc.args or {}),
                        ))
        except Exception:
            # Graceful degradation — surface whatever text we managed to grab.
            pass

        stop = "tool_use" if tool_calls else "end_turn"
        return LLMResponse(
            text=text_out,
            provider=self.name,
            model=self.model,
            tool_calls=tool_calls,
            stop_reason=stop,
        )

    def ping(self) -> None:
        self._client.models.generate_content(model=self.model, contents="ping")
