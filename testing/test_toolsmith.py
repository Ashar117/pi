"""Tests for T-208: toolsmith self-extension procedure."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = _ROOT / "docs" / "templates" / "tool_module_template.py"


# ── Template file exists and is valid Python ──────────────────────────────────

def test_template_exists():
    assert _TEMPLATE.exists()


def test_template_valid_python():
    source = _TEMPLATE.read_text(encoding="utf-8")
    compile(source, str(_TEMPLATE), "exec")  # raises SyntaxError if invalid


def test_template_exports_tools_list():
    spec = importlib.util.spec_from_file_location("tool_template", _TEMPLATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert hasattr(mod, "TOOLS")
    assert isinstance(mod.TOOLS, list)
    assert len(mod.TOOLS) >= 1


def test_template_toolspec_has_name():
    spec = importlib.util.spec_from_file_location("tool_template", _TEMPLATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    tool = mod.TOOLS[0]
    assert hasattr(tool, "name")
    assert tool.name  # non-empty


def test_template_toolspec_has_handler():
    spec = importlib.util.spec_from_file_location("tool_template", _TEMPLATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    tool = mod.TOOLS[0]
    assert callable(tool.handler)


def test_template_toolspec_no_duplicate_name_in_registry():
    """Template's default name 'toolname' must NOT already exist in the live registry."""
    spec = importlib.util.spec_from_file_location("tool_template", _TEMPLATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    template_name = mod.TOOLS[0].name

    from agent.tools import get_tool_definitions
    live_names = {t["name"] for t in get_tool_definitions()}
    assert template_name not in live_names, (
        f"Template default name '{template_name}' conflicts with a live tool — "
        "change the template placeholder"
    )


# ── ADR-008 exists ────────────────────────────────────────────────────────────

def test_adr_008_exists():
    adr = _ROOT / "docs" / "adr" / "008-self-extension-toolsmith.md"
    assert adr.exists()


def test_adr_008_mentions_procedure():
    adr = _ROOT / "docs" / "adr" / "008-self-extension-toolsmith.md"
    content = adr.read_text(encoding="utf-8")
    assert "TOOLSMITH" in content
    assert "run_verify" in content
    assert "no new dependencies" in content.lower() or "No new dependencies" in content


# ── Consciousness has the procedure ──────────────────────────────────────────

def test_consciousness_mentions_toolsmith():
    consciousness = _ROOT / "prompts" / "consciousness.txt"
    content = consciousness.read_text(encoding="utf-8")
    assert "TOOLSMITH" in content
    assert "run_verify" in content


def test_consciousness_references_adr():
    consciousness = _ROOT / "prompts" / "consciousness.txt"
    content = consciousness.read_text(encoding="utf-8")
    assert "ADR-008" in content
