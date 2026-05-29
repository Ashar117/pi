"""testing/test_skill_triage.py — tests for LLM-backed passive skill triage."""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── triage() returns "" when no providers available ──────────────────────────

def test_triage_no_providers_returns_empty():
    from agent import skill_triage

    # Patch _ensure_env_loaded to no-op so the helper can't repopulate keys from .env
    with patch.object(skill_triage, "_ensure_env_loaded", lambda: None), \
         patch.dict(os.environ, {"GROQ_API_KEY": "", "ANTHROPIC_API_KEY": ""}, clear=False):
        os.environ.pop("GROQ_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        result = skill_triage.triage(
            skill_name="test",
            findings_summary="x",
            raw_lines=["a", "b"],
        )

    assert result == ""


# ── triage() returns markdown when Groq succeeds ──────────────────────────────

def test_triage_uses_groq_when_available():
    from agent import skill_triage

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="**Top concerns:**\n- foo bar"))]

    class _FakeGroqClient:
        def __init__(self, *a, **kw): pass
        @property
        def chat(self):
            class _Comp:
                @staticmethod
                def create(**kw): return fake_resp
            class _C:
                completions = _Comp()
            return _C()

    with patch.dict(os.environ, {"GROQ_API_KEY": "test"}), \
         patch.object(skill_triage, "_try_groq", return_value="**Top concerns:**\n- foo bar"):
        result = skill_triage.triage(
            skill_name="test",
            findings_summary="x",
            raw_lines=["a", "b"],
        )

    assert "## Triage" in result
    assert "Top concerns" in result


# ── triage() falls back to Haiku when Groq fails ─────────────────────────────

def test_triage_falls_back_to_haiku():
    from agent import skill_triage

    with patch.object(skill_triage, "_try_groq", return_value=None), \
         patch.object(skill_triage, "_try_haiku", return_value="**Top concerns:**\n- haiku result"):
        result = skill_triage.triage(
            skill_name="test",
            findings_summary="x",
            raw_lines=["a"],
        )

    assert "haiku result" in result


# ── use_haiku=True skips Groq ─────────────────────────────────────────────────

def test_triage_use_haiku_skips_groq():
    from agent import skill_triage

    groq_called = []
    haiku_called = []

    def fake_groq(prompt, max_tokens=400):
        groq_called.append(1)
        return "groq result"

    def fake_haiku(prompt, max_tokens=400):
        haiku_called.append(1)
        return "haiku result"

    with patch.object(skill_triage, "_try_groq", fake_groq), \
         patch.object(skill_triage, "_try_haiku", fake_haiku):
        result = skill_triage.triage(
            skill_name="test",
            findings_summary="x",
            raw_lines=["a"],
            use_haiku=True,
        )

    assert not groq_called
    assert haiku_called
    assert "haiku result" in result


# ── deep_analysis uses Haiku directly ────────────────────────────────────────

def test_deep_analysis_uses_haiku():
    from agent import skill_triage

    with patch.object(skill_triage, "_try_haiku", return_value="- pattern A\n- pattern B"):
        result = skill_triage.deep_analysis(
            skill_name="test",
            context="data here",
            question="what patterns?",
        )

    assert "Deep Analysis" in result
    assert "pattern A" in result


def test_deep_analysis_returns_empty_when_haiku_fails():
    from agent import skill_triage

    with patch.object(skill_triage, "_try_haiku", return_value=None):
        result = skill_triage.deep_analysis(
            skill_name="test",
            context="x",
            question="y",
        )

    assert result == ""


# ── line truncation ───────────────────────────────────────────────────────────

def test_truncate_lines_respects_n():
    from agent.skill_triage import _truncate_lines

    lines = [f"line {i}" for i in range(100)]
    result = _truncate_lines(lines, n=10)
    assert "line 0" in result
    assert "line 9" in result
    assert "line 10" not in result
    assert "90 more" in result


def test_truncate_lines_no_truncation_when_under():
    from agent.skill_triage import _truncate_lines

    lines = ["a", "b", "c"]
    result = _truncate_lines(lines, n=10)
    assert "more" not in result
    assert result == "a\nb\nc"
