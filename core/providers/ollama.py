"""core/providers/ollama.py — Local Ollama provider with tool-fence parsing (T-082 step 5).

Ollama exposes an OpenAI-compatible /api/chat endpoint, but the open-weights
models it hosts generally do not emit OpenAI-style tool_call blocks. They
follow the system prompt's instruction to emit a `````tool\\n{...}\\n````` JSON
fence. This provider parses that fence and returns a unified LLMResponse so
the rest of the agent stays format-agnostic.

The system prompt is responsible for teaching the model the fence format.
This provider is just a translator + HTTP client.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Dict, List

from core.llm_router import LLMResponse, ToolCall
from core.schema_translate import anthropic_messages_to_openai

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False

_TOOL_RE = re.compile(r"```tool\s*\n(.*?)\n```", re.DOTALL)


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        api_key: str = "",
        model: str = "dolphin-mistral",
        host: str = "http://localhost:11434",
    ):
        if not _HTTPX_OK:
            raise ImportError("httpx is required for OllamaProvider")
        self.model = model
        self.host = host.rstrip("/")

    @staticmethod
    def _flatten_tool_results(messages: List[Dict]) -> List[Dict]:
        """Convert Anthropic tool_result blocks into plain user-text messages.

        Most Ollama-hosted open-weights models reject role="tool" messages
        (which the default OpenAI translator emits) — they expect tool results
        inline as user text following the prior assistant turn. We pre-walk
        the message list to flatten any user-role content lists containing
        tool_result blocks into a single user text message with a clear
        `[tool_result for <id>]` prefix. Untouched: assistant messages and
        plain-string user messages.
        """
        out: List[Dict] = []
        for m in messages:
            content = m.get("content", "")
            if m.get("role") != "user" or not isinstance(content, list):
                out.append(m)
                continue
            text_chunks = []
            for block in content:
                btype = block.get("type") if isinstance(block, dict) else None
                if btype == "text":
                    t = block.get("text", "")
                    if t:
                        text_chunks.append(t)
                elif btype == "tool_result":
                    tid = block.get("tool_use_id", "")
                    bcontent = block.get("content", "")
                    if isinstance(bcontent, list):
                        bcontent = " ".join(
                            b.get("text", "") for b in bcontent
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    text_chunks.append(f"[tool_result for {tid}]\n{bcontent}")
            out.append({"role": "user", "content": "\n\n".join(text_chunks)})
        return out

    def chat(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        max_tokens: int = 2048,
    ) -> LLMResponse:
        # Reuse the OpenAI flattener — Ollama's /api/chat speaks the same shape.
        # Tools are NOT sent on the wire: open-weights models follow the fence
        # protocol described in the system prompt; sending tool schemas confuses
        # most of them. The schemas remain in the system prompt as text.
        # First flatten tool_result blocks into user-text so the OpenAI translator
        # doesn't emit role="tool" messages Ollama can't consume.
        flat = self._flatten_tool_results(messages)
        wire_messages = anthropic_messages_to_openai(flat, system)
        payload = {
            "model": self.model,
            "messages": wire_messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        r = httpx.post(f"{self.host}/api/chat", json=payload, timeout=180)
        r.raise_for_status()
        text = r.json().get("message", {}).get("content", "") or ""

        m = _TOOL_RE.search(text)
        if not m:
            return LLMResponse(text=text, provider=self.name, model=self.model,
                               stop_reason="end_turn")
        try:
            call = json.loads(m.group(1))
        except json.JSONDecodeError:
            return LLMResponse(text=text, provider=self.name, model=self.model,
                               stop_reason="end_turn")

        visible = _TOOL_RE.sub("", text).strip()
        return LLMResponse(
            text=visible,
            provider=self.name,
            model=self.model,
            tool_calls=[ToolCall(
                id=str(uuid.uuid4()),
                name=call.get("name", ""),
                input=call.get("args", {}),
            )],
            stop_reason="tool_use",
        )

    def ping(self) -> None:
        r = httpx.get(f"{self.host}/api/tags", timeout=2)
        r.raise_for_status()
