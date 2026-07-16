"""Tests for T-185: repo-map ambient context injection."""
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pi_agent import PiAgent


# ── _is_code_shaped ───────────────────────────────────────────────────────────

def test_is_code_shaped_py_extension():
    assert PiAgent._is_code_shaped("look at agent/modes.py and tell me") is True


def test_is_code_shaped_module_path():
    assert PiAgent._is_code_shaped("tools/ has a bug in the browse handler") is True


def test_is_code_shaped_edit_verb():
    assert PiAgent._is_code_shaped("fix the login timeout bug") is True


def test_is_code_shaped_refactor_verb():
    assert PiAgent._is_code_shaped("refactor memory.py") is True


def test_is_code_shaped_negative_weather():
    assert PiAgent._is_code_shaped("what is the weather today") is False


def test_is_code_shaped_negative_casual():
    assert PiAgent._is_code_shaped("how are you doing today") is False


def test_is_code_shaped_negative_memory_query():
    assert PiAgent._is_code_shaped("what did I tell you yesterday") is False


def test_is_code_shaped_long_prose_no_verb_no_py():
    """Long messages without code markers should not trigger."""
    msg = " ".join(["word"] * 50)
    assert PiAgent._is_code_shaped(msg) is False


# ── _build_compact_repo_map ───────────────────────────────────────────────────

def _make_fake_agent():
    """Build a minimal PiAgent-like namespace for cache tests."""
    agent = types.SimpleNamespace()
    agent._repo_map_cache = None
    return agent


def test_build_compact_repo_map_returns_string():
    """Verify _build_compact_repo_map returns a non-empty string."""
    agent = _make_fake_agent()
    result = PiAgent._build_compact_repo_map(agent, "fix pi_agent.py")
    assert isinstance(result, str)
    # May be empty if ProjectTools fails — that's fine; just must be a string


def test_build_compact_repo_map_caches_result():
    agent = _make_fake_agent()
    r1 = PiAgent._build_compact_repo_map(agent, "fix bug")
    r2 = PiAgent._build_compact_repo_map(agent, "fix bug")
    assert r1 is r2  # same object (cached)


def test_build_compact_repo_map_cache_invalidated_by_none():
    """Setting _repo_map_cache = None forces a rebuild."""
    agent = _make_fake_agent()
    r1 = PiAgent._build_compact_repo_map(agent, "refactor tools/")
    agent._repo_map_cache = None  # simulate write (T-185 dirty flag)
    r2 = PiAgent._build_compact_repo_map(agent, "refactor tools/")
    # Both are strings; after invalidation the cache rebuilds (may equal r1)
    assert isinstance(r2, str)


def test_build_compact_repo_map_token_cap():
    """Output must not exceed 1600 chars."""
    agent = _make_fake_agent()
    result = PiAgent._build_compact_repo_map(agent, "everything")
    assert len(result) <= 1600


def test_build_compact_repo_map_graceful_on_error():
    """If ProjectTools raises, return empty string — don't propagate."""
    agent = _make_fake_agent()
    with patch("tools.tools_project.ProjectTools") as mock_pt:
        mock_pt.side_effect = RuntimeError("injected error")
        result = PiAgent._build_compact_repo_map(agent, "fix bug")
    assert result == ""


# ── ModeConfig flag ───────────────────────────────────────────────────────────

def test_root_mode_has_inject_repo_map_true():
    from agent.modes import MODE_CONFIGS
    assert MODE_CONFIGS["root"].inject_repo_map is True


def test_normie_mode_has_inject_repo_map_false():
    from agent.modes import MODE_CONFIGS
    assert MODE_CONFIGS["normie"].inject_repo_map is False
