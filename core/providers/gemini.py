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

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        # T-212: gemini-2.0-flash free tier was retired (429 RESOURCE_EXHAUSTED,
        # free_tier limit 0). GEMINI_MODEL env override lets Ash point at a model
        # his Google project actually serves WITHOUT a code change (config.py is
        # guarded). Env wins; falls back to the passed/default model.
        import os
        from google import genai
        self._genai = genai
        try:
            # T-237: 30s HTTP timeout so a hung Gemini call doesn't block the router indefinitely.
            self._client = genai.Client(api_key=api_key, http_options={"timeout": 30})
        except Exception:
            self._client = genai.Client(api_key=api_key)
        self.model = os.environ.get("GEMINI_MODEL") or model

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

        # T-212: populate token counts so cost / TPD tracking isn't always 0.
        tokens_in = tokens_out = 0
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            tokens_in = getattr(um, "prompt_token_count", 0) or 0
            tokens_out = getattr(um, "candidates_token_count", 0) or 0

        stop = "tool_use" if tool_calls else "end_turn"
        return LLMResponse(
            text=text_out,
            provider=self.name,
            model=self.model,
            tool_calls=tool_calls,
            stop_reason=stop,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    def grounded_search(self, query: str, max_tokens: int = 2048) -> Dict:
        """T-227: Gemini Google-Search grounding — returns a synthesized answer + citations.

        Returns:
            {"answer": str, "citations": [{"title": str, "url": str}], "tokens_in": int, "tokens_out": int}
        Raises on any Gemini error so the caller can fall back to web_search.
        """
        from google.genai import types

        tool = types.Tool(google_search=types.GoogleSearch())
        resp = self._client.models.generate_content(
            model=self.model,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[tool],
                max_output_tokens=max_tokens,
            ),
        )

        answer = ""
        try:
            candidate = (resp.candidates or [None])[0]
            if candidate and candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if getattr(part, "text", None):
                        answer += part.text
        except Exception:
            pass

        citations: List[Dict] = []
        try:
            gm = getattr(resp.candidates[0], "grounding_metadata", None) if resp.candidates else None
            if gm and getattr(gm, "grounding_chunks", None):
                for chunk in gm.grounding_chunks:
                    web = getattr(chunk, "web", None)
                    if web:
                        citations.append({
                            "title": getattr(web, "title", None) or "",
                            "url": getattr(web, "uri", None) or "",
                        })
        except Exception:
            pass

        tokens_in = tokens_out = 0
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            tokens_in = getattr(um, "prompt_token_count", 0) or 0
            tokens_out = getattr(um, "candidates_token_count", 0) or 0

        return {"answer": answer, "citations": citations, "tokens_in": tokens_in, "tokens_out": tokens_out}

    def ping(self) -> None:
        self._client.models.generate_content(model=self.model, contents="ping")
