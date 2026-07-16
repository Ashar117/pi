"""testing/test_thinking.py — T-124-lite: thinking layer tests."""
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── bypass commands ─────────────────────────────────────────────────────────

def test_should_bypass_known_commands():
    from agent.thinking import should_bypass
    for cmd in ["exit", "/exit", "help", "clear", "normie mode", "root", "research"]:
        assert should_bypass(cmd), f"{cmd} should bypass"


def test_should_bypass_empty():
    from agent.thinking import should_bypass
    assert should_bypass("")
    assert should_bypass("   ")


def test_normal_input_does_not_bypass():
    from agent.thinking import should_bypass
    assert not should_bypass("hello")
    assert not should_bypass("can you check the weather?")
    # Long input still passes through (Ash override)
    assert not should_bypass("x" * 500)


# ── parse response ──────────────────────────────────────────────────────────

def test_parse_clean_json():
    from agent.thinking import _parse_response
    raw = '{"intent": "info", "normalised_query": "weather query", "confidence": 0.9}'
    parsed = _parse_response(raw)
    assert parsed["intent"] == "info"
    assert parsed["confidence"] == 0.9


def test_parse_with_code_fence():
    from agent.thinking import _parse_response
    raw = '```json\n{"intent": "greeting", "normalised_query": "hi", "confidence": 0.95}\n```'
    parsed = _parse_response(raw)
    assert parsed["intent"] == "greeting"


def test_parse_with_extra_text():
    from agent.thinking import _parse_response
    raw = 'Sure, here:\n{"intent": "info", "normalised_query": "q", "confidence": 0.7}\nAnything else?'
    parsed = _parse_response(raw)
    assert parsed["intent"] == "info"


def test_parse_missing_key_returns_none():
    from agent.thinking import _parse_response
    raw = '{"intent": "info", "confidence": 0.5}'  # missing normalised_query
    assert _parse_response(raw) is None


def test_parse_garbage_returns_none():
    from agent.thinking import _parse_response
    assert _parse_response("not json at all") is None
    assert _parse_response("") is None


def test_confidence_clamped():
    from agent.thinking import _parse_response
    raw = '{"intent": "info", "normalised_query": "q", "confidence": 1.5}'
    parsed = _parse_response(raw)
    assert parsed["confidence"] == 1.0
    raw2 = '{"intent": "info", "normalised_query": "q", "confidence": -0.3}'
    parsed2 = _parse_response(raw2)
    assert parsed2["confidence"] == 0.0


# ── normalise: bypass returns None ──────────────────────────────────────────

def test_normalise_bypass_returns_none():
    from agent.thinking import normalise
    assert normalise("exit") is None
    assert normalise("") is None


# ── normalise: Groq success ──────────────────────────────────────────────────

def test_normalise_uses_groq_when_available():
    from agent import thinking
    fake = '{"intent": "complaint", "normalised_query": "subway order had sauce", "confidence": 0.85}'
    with patch.object(thinking, "_try_groq", return_value=fake), \
         patch.object(thinking, "_try_haiku", return_value=None):
        result = thinking.normalise("no sauce in subway aghhh")
    assert result["intent"] == "complaint"
    assert "subway" in result["normalised_query"]


# ── normalise: Groq fail → Haiku fallback ───────────────────────────────────

def test_normalise_falls_back_to_haiku():
    from agent import thinking
    fake = '{"intent": "info", "normalised_query": "weather", "confidence": 0.9}'
    with patch.object(thinking, "_try_groq", return_value=None), \
         patch.object(thinking, "_try_haiku", return_value=fake):
        result = thinking.normalise("how is the weather")
    assert result["intent"] == "info"


# ── normalise: both fail → None ──────────────────────────────────────────────

def test_normalise_both_fail_returns_none():
    from agent import thinking
    with patch.object(thinking, "_try_groq", return_value=None), \
         patch.object(thinking, "_try_haiku", return_value=None):
        result = thinking.normalise("hello")
    assert result is None


# ── normalise: long input still thinks (Ash override) ───────────────────────

def test_long_input_still_thinks():
    from agent import thinking
    fake = '{"intent": "info", "normalised_query": "long query", "confidence": 0.9}'
    long_text = "tell me " + ("about Pi " * 50)
    with patch.object(thinking, "_try_groq", return_value=fake):
        result = thinking.normalise(long_text)
    assert result is not None  # NOT bypassed despite >200 chars


# ── format_thinking_block ───────────────────────────────────────────────────

def test_format_thinking_block_renders():
    from agent.thinking import format_thinking_block
    block = format_thinking_block({
        "intent": "complaint",
        "normalised_query": "user upset about X",
        "confidence": 0.8,
    })
    assert "THINKING LAYER" in block
    assert "complaint" in block
    assert "user upset about X" in block


def test_format_thinking_block_low_confidence_warns():
    from agent.thinking import format_thinking_block
    block = format_thinking_block({
        "intent": "clarification",
        "normalised_query": "unclear",
        "confidence": 0.3,
    })
    assert "confidence low" in block


# ── T-129: extended thinking (referenced_memories + ask_clarifier) ───────────

def test_lite_response_still_parses_with_default_extended_fields():
    """Backward compat: a 3-field (lite) response parses, new fields default."""
    from agent.thinking import _parse_response
    raw = '{"intent": "info", "normalised_query": "q", "confidence": 0.9}'
    parsed = _parse_response(raw)
    assert parsed["referenced_memories"] == []
    assert parsed["ask_clarifier"] is None


def test_extended_output_includes_referenced_memories():
    from agent.thinking import _parse_response
    raw = ('{"intent": "info", "normalised_query": "the deadline", "confidence": 0.9, '
           '"referenced_memories": ["abc123", "def456"], "ask_clarifier": null}')
    parsed = _parse_response(raw)
    assert parsed["referenced_memories"] == ["abc123", "def456"]
    assert parsed["ask_clarifier"] is None


def test_clarifier_generated_when_confidence_low():
    from agent.thinking import _parse_response
    raw = ('{"intent": "clarification", "normalised_query": "wdym", "confidence": 0.3, '
           '"referenced_memories": [], "ask_clarifier": "Which order do you mean — the subway one or the pizza one?"}')
    parsed = _parse_response(raw)
    assert parsed["ask_clarifier"].startswith("Which order")


def test_recall_hits_appear_in_prompt():
    from agent.thinking import _build_prompt
    hits = [{"id": "mem-xyz", "content": "GNN paper deadline is June 10"}]
    prompt = _build_prompt("when is it due", history=None, recall_hits=hits)
    assert "mem-xyz" in prompt and "GNN paper deadline" in prompt


def test_normalise_extended_via_groq():
    from agent import thinking
    fake = ('{"intent": "info", "normalised_query": "gnn deadline", "confidence": 0.9, '
            '"referenced_memories": ["mem-xyz"], "ask_clarifier": null}')
    hits = [{"id": "mem-xyz", "content": "GNN paper deadline is June 10"}]
    with patch.object(thinking, "_try_groq", return_value=fake), \
         patch.object(thinking, "_try_haiku", return_value=None):
        result = thinking.normalise("when is it due", recall_hits=hits)
    assert result["referenced_memories"] == ["mem-xyz"]


def test_format_block_renders_clarifier_and_refs():
    from agent.thinking import format_thinking_block
    block = format_thinking_block({
        "intent": "info", "normalised_query": "q", "confidence": 0.4,
        "referenced_memories": ["m1", "m2"],
        "ask_clarifier": "Do you mean the budget or the timeline?",
    })
    assert "referenced_memories: m1, m2" in block
    assert "Do you mean the budget" in block
