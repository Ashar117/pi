"""core/schema_translate.py — Convert tool schemas between Anthropic / OpenAI / Gemini formats (T-048).

Canonical format = Anthropic (input_schema key).
OpenAI/Groq uses "parameters" key inside a "function" wrapper.
Gemini uses google.generativeai FunctionDeclaration objects.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from core.llm_router import ToolCall


# ── Anthropic → OpenAI (Groq) ─────────────────────────────────────────────────

def anthropic_to_openai_tools(tools: List[Dict]) -> List[Dict]:
    """Convert Anthropic tool definitions to OpenAI function-calling format."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }),
            },
        })
    return result


def openai_tool_calls_to_unified(tool_calls: Any) -> List[ToolCall]:
    """Convert OpenAI-format tool_calls to unified ToolCall list."""
    result = []
    for tc in (tool_calls or []):
        fn = tc.function
        try:
            args = json.loads(fn.arguments) if isinstance(fn.arguments, str) else (fn.arguments or {})
        except Exception:
            args = {}
        result.append(ToolCall(
            id=tc.id or str(uuid.uuid4()),
            name=fn.name,
            input=args,
        ))
    return result


# ── Anthropic → Gemini ────────────────────────────────────────────────────────

def anthropic_to_gemini_tools(tools: List[Dict]) -> Any:
    """Convert Anthropic tool definitions to a Gemini Tool list.

    T-076: now uses the ``google-genai`` SDK (the old ``google-generativeai``
    package was retired). Returns None if the SDK is not installed.
    """
    if not tools:
        return None
    try:
        from google.genai import types

        declarations = []
        for t in tools:
            schema = t.get("input_schema", {"type": "object", "properties": {}})
            declarations.append(types.FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=schema,
            ))
        return [types.Tool(function_declarations=declarations)]
    except Exception:
        return None


# ── Anthropic message list → OpenAI message list ──────────────────────────────

def anthropic_messages_to_openai(messages: List[Dict], system: str) -> List[Dict]:
    """Flatten Anthropic canonical messages into OpenAI-compatible message list.

    Anthropic groups tool-results under a "user" role with a list content;
    OpenAI sends each tool result as a separate "tool" role message.
    """
    out: List[Dict] = [{"role": "system", "content": system}]
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            out.append({"role": role, "content": str(content)})
            continue

        # content is a list of blocks
        if role == "assistant":
            # May contain text + tool_use blocks
            text_parts = []
            tool_calls_out = []
            for block in content:
                btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
                if btype == "text":
                    t = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                    if t:
                        text_parts.append(t)
                elif btype == "tool_use":
                    bid = block.get("id", "") if isinstance(block, dict) else getattr(block, "id", "")
                    bname = block.get("name", "") if isinstance(block, dict) else getattr(block, "name", "")
                    binput = block.get("input", {}) if isinstance(block, dict) else getattr(block, "input", {})
                    tool_calls_out.append({
                        "id": bid,
                        "type": "function",
                        "function": {
                            "name": bname,
                            "arguments": json.dumps(binput),
                        },
                    })
            msg: Dict[str, Any] = {"role": "assistant", "content": " ".join(text_parts) or None}
            if tool_calls_out:
                msg["tool_calls"] = tool_calls_out
            out.append(msg)

        elif role == "user":
            # May contain tool_result blocks
            tool_results = []
            user_text_parts = []
            for block in content:
                btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
                if btype == "tool_result":
                    tid = block.get("tool_use_id", "") if isinstance(block, dict) else getattr(block, "tool_use_id", "")
                    bcontent = block.get("content", "") if isinstance(block, dict) else getattr(block, "content", "")
                    if isinstance(bcontent, list):
                        bcontent = " ".join(
                            b.get("text", "") for b in bcontent
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tid,
                        "content": str(bcontent),
                    })
                elif btype == "text":
                    t = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                    if t:
                        user_text_parts.append(t)
            if user_text_parts:
                out.append({"role": "user", "content": " ".join(user_text_parts)})
            out.extend(tool_results)

    return out
