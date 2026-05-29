"""testing/test_cost_footer.py — T-130: per-turn inline cost footer.

Pure formatter + env-gated emit. No agent instantiation needed for most tests.
"""
import io
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.cost_footer import format_cost_footer, is_enabled, emit_if_enabled


# ── Pure formatter ────────────────────────────────────────────────────────────

def test_format_basic():
    line = format_cost_footer(0.0034, 8120, 1240, "anthropic/claude-sonnet-4-6", 1.4)
    assert line.startswith("[$")
    assert "0.0034" in line
    assert "8120 tok in" in line
    assert "1240 out" in line
    assert "anthropic/claude-sonnet-4-6" in line
    assert "1.4s" in line
    assert line.endswith("]")


def test_format_zero_cost():
    line = format_cost_footer(0.0, 0, 0, "groq/llama-3.3-70b", 0.3)
    assert "$0.0000" in line
    assert "0 tok in" in line


def test_format_empty_model_falls_back():
    line = format_cost_footer(0.01, 100, 50, "", 0.5)
    assert "unknown" in line


def test_format_handles_floats_as_token_counts():
    # Some routers report tokens as floats — should render as ints.
    line = format_cost_footer(0.001, 100.7, 50.3, "x/y", 1.0)
    assert "100 tok in" in line
    assert "50 out" in line


# ── Env-gated emit ────────────────────────────────────────────────────────────

def test_is_enabled_off_by_default(monkeypatch):
    monkeypatch.delenv("PI_SHOW_COST", raising=False)
    assert is_enabled() is False


def test_is_enabled_on(monkeypatch):
    monkeypatch.setenv("PI_SHOW_COST", "on")
    assert is_enabled() is True


def test_is_enabled_case_insensitive(monkeypatch):
    monkeypatch.setenv("PI_SHOW_COST", "ON")
    assert is_enabled() is True


def test_is_enabled_other_values(monkeypatch):
    for v in ("0", "1", "off", "true", "yes", "enabled"):
        monkeypatch.setenv("PI_SHOW_COST", v)
        assert is_enabled() is False, f"{v!r} should not enable"


def test_emit_writes_to_stream_when_enabled(monkeypatch):
    monkeypatch.setenv("PI_SHOW_COST", "on")
    buf = io.StringIO()
    line = emit_if_enabled(0.005, 1000, 200, "test/model", 0.7, stream=buf)
    assert line is not None
    assert line in buf.getvalue()
    assert "1000 tok in" in buf.getvalue()


def test_emit_returns_none_when_disabled(monkeypatch):
    monkeypatch.delenv("PI_SHOW_COST", raising=False)
    buf = io.StringIO()
    line = emit_if_enabled(0.005, 1000, 200, "test/model", 0.7, stream=buf)
    assert line is None
    assert buf.getvalue() == ""


def test_emit_never_raises_on_stream_failure(monkeypatch):
    """A broken stream must not propagate — observability is best-effort."""
    monkeypatch.setenv("PI_SHOW_COST", "on")

    class _BrokenStream:
        def write(self, *_a, **_kw): raise IOError("nope")
        def flush(self): raise IOError("nope")

    line = emit_if_enabled(0.005, 1000, 200, "test/model", 0.7, stream=_BrokenStream())
    assert line is None  # silent failure


# ── Integration-shape: ensure footer doesn't pollute final_text ──────────────

def test_footer_goes_to_stderr_not_returned_value(monkeypatch, capsys):
    """When emit_if_enabled fires, output lands on the chosen stream — never
    in the function's return value (which is the line itself, but the CALLER
    in pi_agent doesn't use the return). This test guards against accidental
    refactors that splice the footer into final_text."""
    monkeypatch.setenv("PI_SHOW_COST", "on")
    line = emit_if_enabled(0.001, 10, 5, "x/y", 0.1)
    # Goes to actual sys.stderr by default — captured by capsys
    captured = capsys.readouterr()
    assert line in captured.err
    assert line not in captured.out  # NEVER stdout — Telegram/voice would see stdout
