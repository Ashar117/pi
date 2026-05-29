"""testing/test_tool_input_validation.py — T-107: schema validation before tool dispatch."""
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_spec(schema):
    from agent.tool_spec import ToolSpec
    return ToolSpec(
        name="test_tool",
        description="test",
        input_schema=schema,
        handler=lambda agent, inp, **kw: {"success": True, "got": inp},
    )


def _make_agent():
    agent = MagicMock()
    agent.evolution.track_pattern = MagicMock()
    return agent


# ── Missing required key ──────────────────────────────────────────────────────

def test_missing_required_key_returns_structured_error():
    from agent.tools import execute_tool, _VALIDATORS
    _VALIDATORS.clear()

    spec = _make_spec({
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    })

    agent = _make_agent()
    with patch("agent.tools._registry", return_value={"test_tool": spec}), \
         patch("agent.observability.track_silent"):
        result = execute_tool(agent, "test_tool", {})

    assert result["error"] == "invalid_input"
    assert result["tool"] == "test_tool"
    assert any("query" in str(e) for e in result["schema_mismatch"])
    assert "expected_schema" in result


# ── Wrong type ────────────────────────────────────────────────────────────────

def test_wrong_type_returns_structured_error():
    from agent.tools import execute_tool, _VALIDATORS
    _VALIDATORS.clear()

    spec = _make_spec({
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    })

    agent = _make_agent()
    with patch("agent.tools._registry", return_value={"test_tool": spec}), \
         patch("agent.observability.track_silent"):
        result = execute_tool(agent, "test_tool", {"query": 123})

    assert result["error"] == "invalid_input"
    assert result["schema_mismatch"]


# ── Invalid enum value ────────────────────────────────────────────────────────

def test_invalid_enum_value():
    from agent.tools import execute_tool, _VALIDATORS
    _VALIDATORS.clear()

    spec = _make_spec({
        "type": "object",
        "properties": {
            "tier": {"type": "string", "enum": ["l1", "l2", "l3"]},
        },
        "required": ["tier"],
    })

    agent = _make_agent()
    with patch("agent.tools._registry", return_value={"test_tool": spec}), \
         patch("agent.observability.track_silent"):
        result = execute_tool(agent, "test_tool", {"tier": "l99"})

    assert result["error"] == "invalid_input"
    assert result["schema_mismatch"]


# ── Valid input passes through ────────────────────────────────────────────────

def test_valid_input_passes_through():
    from agent.tools import execute_tool, _VALIDATORS
    _VALIDATORS.clear()

    spec = _make_spec({
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    })

    agent = _make_agent()
    with patch("agent.tools._registry", return_value={"test_tool": spec}):
        result = execute_tool(agent, "test_tool", {"query": "foo"})

    assert result.get("error") != "invalid_input"
    assert result["got"] == {"query": "foo"}


# ── Validator is cached per spec ─────────────────────────────────────────────

def test_validator_cached():
    import jsonschema
    from agent.tools import execute_tool, _validate_tool_input, _VALIDATORS
    _VALIDATORS.clear()

    spec = _make_spec({
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    })

    compile_calls = []
    real_validator = jsonschema.Draft7Validator

    def counting_validator(schema, *a, **kw):
        compile_calls.append(1)
        return real_validator(schema, *a, **kw)

    with patch("jsonschema.Draft7Validator", side_effect=counting_validator):
        for _ in range(10):
            _validate_tool_input(spec, {"q": "x"}, "test_tool")

    assert len(compile_calls) == 1, f"Expected 1 compile, got {len(compile_calls)}"


# ── Validation failure logged via track_silent ────────────────────────────────

def test_validation_failure_logged():
    from agent.tools import execute_tool, _VALIDATORS
    _VALIDATORS.clear()

    spec = _make_spec({
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    })

    recorded = []

    def fake_track(cat, exc=None, **kw):
        recorded.append(cat)

    agent = _make_agent()
    with patch("agent.tools._registry", return_value={"test_tool": spec}), \
         patch("agent.observability.track_silent", fake_track):
        execute_tool(agent, "test_tool", {})

    assert "tools.invalid_input" in recorded
