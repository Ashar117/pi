"""agent/thinking.py — T-124-lite: pre-response intent + query normalisation.

A cheap deliberation step that runs between bubble close and main-model
dispatch. Two jobs only (lite version):
  1. Classify intent (greeting, complaint, info, action, clarification, other)
  2. Normalise the query (typo-correct, resolve obvious references)

Plus a confidence score so the main model can ask a clarifier when confused.

Provider chain: Groq llama-3.3-70b primary → Claude Haiku 4.5 fallback →
skip silently (raw input passes through unchanged). Mirrors the T-092
compression pattern; uses the T-121 provider router shape.

ALWAYS ON for non-command bubbles (Ash decision). Direct commands
(exit/help/mode switches) bypass since they aren't thoughts.

Recall-merge, explicit clarifier generation, and memory-id extraction
are deferred to T-129 (T-124-full).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# Commands that should NEVER pass through the thinking layer
_BYPASS_COMMANDS = {
    "exit", "/exit", "quit", "bye",
    "help", "/help",
    "clear", "/clear",
    "root mode", "root", "normie mode", "normie",
    "research mode", "research",
    "voice", "voice ptt", "voice vad", "voice wake",
    "briefing", "analyze performance",
}


def _ensure_env_loaded() -> None:
    if os.environ.get("GROQ_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except Exception:
        pass


def _track(event: str, exc: Optional[Exception] = None, **context) -> None:
    try:
        from agent.observability import track_silent
        track_silent(f"thinking.{event}", exc, context=context)
    except Exception:
        pass


def should_bypass(text: str) -> bool:
    """Return True if this input should skip the thinking layer entirely."""
    if not text or not text.strip():
        return True
    lowered = text.strip().lower()
    if lowered in _BYPASS_COMMANDS:
        return True
    # Tool result echoes (heuristic) — pi_agent passes these to thinking layer too
    # only if they look like user-facing text. Tool results typically start with
    # structured prefixes like "[" or "{" — but harmless to think about them.
    return False


# T-129 (full): prompt now also merges recall hits → referenced_memories (cite
# ids) and generates an explicit clarifier question. The two extra fields are
# optional in the parser, so a lite-shaped response (3 core fields) still works
# (backward compat / fallback).
_PROMPT = """You are an intent classifier preprocessing input for an AI assistant named Pi.

Recent history (last 3 turns):
{history}

Relevant memories that may be referenced (id: content):
{recall_hits}

User just said: {text}

Classify the intent, normalise the query, and identify referenced memories. Output JSON ONLY:
{{
  "intent": "greeting" | "complaint" | "info" | "action" | "clarification" | "other",
  "normalised_query": "what the user is actually asking — typo-corrected, references resolved into full nouns. Keep concise.",
  "confidence": 0.0..1.0,
  "referenced_memories": ["<id from the list above that the user is actually referring to>"],
  "ask_clarifier": "ONE concrete clarifying question, or null"
}}

referenced_memories: cite ONLY ids from the list above that the user's message refers to; [] if none.
ask_clarifier: if the input is ambiguous (e.g. 'wdym', 'huh') set confidence < 0.6 and generate ONE specific question (never 'can you clarify'); otherwise null."""


def _format_recall_hits(recall_hits: Optional[List[Dict[str, Any]]]) -> str:
    if not recall_hits:
        return "(no recall hits)"
    lines = []
    for h in recall_hits[:8]:
        hid = str(h.get("id", ""))[:12]
        content = (h.get("content") or "")[:80]
        lines.append(f"{hid}: {content}")
    return "\n".join(lines) or "(no recall hits)"


def _build_prompt(text: str, history: Optional[List[str]] = None,
                  recall_hits: Optional[List[Dict[str, Any]]] = None) -> str:
    hist_str = "\n".join((history or [])[-3:]) or "(no prior turns)"
    return _PROMPT.format(history=hist_str, text=text,
                          recall_hits=_format_recall_hits(recall_hits))


def _try_groq(prompt: str, max_tokens: int = 200) -> Optional[str]:
    _ensure_env_loaded()
    if not os.environ.get("GROQ_API_KEY"):
        return None
    try:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        _track("groq_failed", e)
        return None


def _try_haiku(prompt: str, max_tokens: int = 200) -> Optional[str]:
    _ensure_env_loaded()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip() if resp.content else None
    except Exception as e:
        _track("haiku_failed", e)
        return None


def _parse_response(raw: str) -> Optional[Dict[str, Any]]:
    """Extract intent/normalised_query/confidence from a (possibly noisy) LLM reply."""
    if not raw:
        return None
    # Strip code fences and find JSON
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to locate {...} substring
        m = re.search(r"\{[\s\S]*?\}", cleaned)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    intent = data.get("intent")
    normalised = data.get("normalised_query")
    confidence = data.get("confidence")
    if intent is None or normalised is None or confidence is None:
        return None
    try:
        confidence_f = float(confidence)
    except (TypeError, ValueError):
        return None

    # T-129: optional extended fields. Absent → lite-shaped defaults (backward compat).
    referenced = data.get("referenced_memories", [])
    if not isinstance(referenced, list):
        referenced = []
    referenced = [str(x) for x in referenced if x not in (None, "")][:10]

    clarifier = data.get("ask_clarifier")
    if clarifier in (None, "", "null", "None"):
        clarifier = None
    else:
        clarifier = str(clarifier)

    return {
        "intent": str(intent),
        "normalised_query": str(normalised),
        "confidence": max(0.0, min(1.0, confidence_f)),
        "referenced_memories": referenced,
        "ask_clarifier": clarifier,
    }


def normalise(text: str, history: Optional[List[str]] = None,
              recall_hits: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Run the thinking layer on a non-command bubble's text.

    Returns {intent, normalised_query, confidence, referenced_memories,
    ask_clarifier} on success (T-129). When recall_hits are supplied, the model
    may cite their ids in referenced_memories. Returns None if both providers
    fail OR if the input should bypass. Never raises.
    """
    if should_bypass(text):
        return None

    prompt = _build_prompt(text, history, recall_hits)

    summary = _try_groq(prompt)
    if summary is None:
        summary = _try_haiku(prompt)

    if not summary:
        _track("both_providers_failed", None, text_prefix=text[:60])
        return None

    parsed = _parse_response(summary)
    if parsed is None:
        _track("parse_failed", None, raw_prefix=summary[:80])
        return None

    return parsed


def format_thinking_block(result: Dict[str, Any]) -> str:
    """Render the thinking layer output as a prefix block for main-model input."""
    if not result:
        return ""
    intent = result.get("intent", "?")
    nq = result.get("normalised_query", "")
    conf = result.get("confidence", 0.0)
    lines = [
        "[THINKING LAYER]",
        f"intent: {intent}",
        f"normalised: {nq}",
        f"confidence: {conf:.2f}",
    ]
    # T-129: surface referenced memory ids + an explicit clarifier when present.
    refs = result.get("referenced_memories") or []
    if refs:
        lines.append(f"referenced_memories: {', '.join(refs)}")
    clarifier = result.get("ask_clarifier")
    if clarifier:
        lines.append(f"clarifier: {clarifier}")
    elif conf < 0.6:
        lines.append("note: confidence low — consider asking a clarifying question")
    return "\n".join(lines)
