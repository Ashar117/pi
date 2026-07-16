"""testing/test_status_line.py — T-131: per-turn status line repaint.

Pure formatter + env-gated emit. No agent instantiation needed for formatter tests.
"""
import io
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.status_line import format_status_line, is_enabled, emit_if_enabled


# ── Pure formatter ────────────────────────────────────────────────────────────

def test_format_root_mode():
    line = format_status_line("root", "a3f2e1c", 14, 0.038, 2, 184)
    assert line.startswith("[root")
    assert "turn 14" in line
    assert "session a3f2e1c" in line
    assert "$0.038 today" in line
    assert "2 open" in line
    assert "L3: 184" in line
    assert line.endswith("]")


def test_format_normie_mode():
    line = format_status_line("normie", "b1c2d3e", 3, 0.001, 5, 42)
    assert "normie" in line
    assert "session b1c2d3e" in line


def test_format_unknown_l3_shows_question_mark():
    line = format_status_line("root", "abc", 1, 0.0, 0, -1)
    assert "L3: ?" in line


def test_format_zero_cost():
    line = format_status_line("root", "xyz", 0, 0.0, 0, 0)
    assert "$0.000 today" in line


# ── Env gate ─────────────────────────────────────────────────────────────────

def test_is_enabled_off_by_default(monkeypatch):
    monkeypatch.delenv("PI_STATUS_LINE", raising=False)
    assert is_enabled() is False


def test_is_enabled_on(monkeypatch):
    monkeypatch.setenv("PI_STATUS_LINE", "on")
    assert is_enabled() is True


def test_is_enabled_case_insensitive(monkeypatch):
    monkeypatch.setenv("PI_STATUS_LINE", "ON")
    assert is_enabled() is True


def test_is_enabled_other_values_off(monkeypatch):
    for v in ("1", "true", "yes", "enabled", "off"):
        monkeypatch.setenv("PI_STATUS_LINE", v)
        assert is_enabled() is False, f"{v!r} should not enable"


# ── emit_if_enabled ──────────────────────────────────────────────────────────

def _make_agent(mode="root", session_id="a1b2c3d4"):
    agent = MagicMock()
    agent.mode = mode
    agent.session_id = session_id
    agent.evolution.get_daily_cost.return_value = 0.025
    mem = MagicMock()
    mem.sqlite_path = None  # no DB in unit tests
    agent.memory = mem
    return agent


def test_emit_skips_when_disabled(monkeypatch, capsys):
    monkeypatch.delenv("PI_STATUS_LINE", raising=False)
    agent = _make_agent()
    with patch("agent.status_line.count_today", return_value=5):
        emit_if_enabled(agent)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_emit_writes_to_stderr_when_enabled(monkeypatch, capsys):
    monkeypatch.setenv("PI_STATUS_LINE", "on")
    agent = _make_agent()
    with patch("agent.status_line.count_today", return_value=7), \
         patch("agent.status_line._count_open_tickets", return_value=3), \
         patch("agent.status_line._count_l3_rows", return_value=99):
        emit_if_enabled(agent)
    captured = capsys.readouterr()
    assert "[root" in captured.err
    assert "turn 7" in captured.err
    assert "3 open" in captured.err
    assert "L3: 99" in captured.err
    assert captured.out == ""  # never stdout


def test_emit_fallback_on_exception(monkeypatch, capsys):
    """If anything in emit fails, '[Pi · status unavailable]' goes to stderr."""
    monkeypatch.setenv("PI_STATUS_LINE", "on")
    agent = _make_agent()
    with patch("agent.status_line.count_today", side_effect=RuntimeError("boom")):
        emit_if_enabled(agent)
    captured = capsys.readouterr()
    assert "unavailable" in captured.err


def test_emit_never_raises_on_broken_agent(monkeypatch):
    """A totally broken agent object must not propagate — observability is best-effort."""
    monkeypatch.setenv("PI_STATUS_LINE", "on")

    class _Broken:
        @property
        def mode(self): raise AttributeError("gone")

    emit_if_enabled(_Broken())  # must not raise
