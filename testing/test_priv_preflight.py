"""T-108: _check_god_mode_available() preflight tests."""
import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_agent():
    """Minimal PiAgent-shaped object that provides _check_god_mode_available."""
    from pi_agent import PiAgent
    agent = PiAgent.__new__(PiAgent)
    agent.session_id = "test"
    agent.session_start = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    agent.mode = "root"
    agent._memory_by_namespace = {}
    agent.memory = MagicMock()
    agent.evolution = MagicMock()
    agent._awareness_refreshing = False
    agent._awareness_refresh_lock = threading.Lock()
    return agent


def _god_db_path():
    return Path(__file__).parent.parent / "data" / "god_memory.db"


# ── Missing DB ────────────────────────────────────────────────────────────────

def test_missing_db_returns_false(tmp_path):
    agent = _make_agent()
    fake_db = tmp_path / "missing.db"  # does not exist

    with patch("agent.modes.get_mode_config") as mock_cfg:
        cfg = MagicMock()
        cfg.memory_db = str(fake_db.relative_to(tmp_path))
        mock_cfg.return_value = cfg

        with patch("pathlib.Path.exists", return_value=False):
            ok, reason = agent._check_god_mode_available()

    assert ok is False
    assert "not found" in reason


# ── GOD_SUPABASE_URL not set ──────────────────────────────────────────────────

def test_missing_god_url_returns_false(tmp_path):
    agent = _make_agent()
    db = tmp_path / "god_memory.db"
    db.touch()

    env = {"GOD_SUPABASE_URL": "", "SUPABASE_URL": "https://pub.supabase.co"}

    with patch("agent.modes.get_mode_config") as mock_cfg:
        cfg = MagicMock()
        cfg.memory_db = str(db)
        mock_cfg.return_value = cfg

        with patch.object(Path, "exists", return_value=True), \
             patch.dict(os.environ, env, clear=False):
            # Remove GOD_SUPABASE_URL if set
            os.environ.pop("GOD_SUPABASE_URL", None)
            ok, reason = agent._check_god_mode_available()

    assert ok is False
    assert "GOD_SUPABASE_URL" in reason


# ── GOD_SUPABASE_URL == SUPABASE_URL ─────────────────────────────────────────

def test_equal_urls_returns_false(tmp_path):
    agent = _make_agent()
    db = tmp_path / "god_memory.db"
    db.touch()

    same_url = "https://same.supabase.co"
    env = {"GOD_SUPABASE_URL": same_url, "SUPABASE_URL": same_url}

    with patch("agent.modes.get_mode_config") as mock_cfg:
        cfg = MagicMock()
        cfg.memory_db = str(db)
        mock_cfg.return_value = cfg

        with patch.object(Path, "exists", return_value=True), \
             patch.dict(os.environ, env):
            ok, reason = agent._check_god_mode_available()

    assert ok is False
    assert "leak" in reason.lower() or "==" in reason


# ── All conditions pass ───────────────────────────────────────────────────────

def test_valid_env_returns_true(tmp_path):
    agent = _make_agent()
    db = tmp_path / "god_memory.db"
    db.touch()

    env = {
        "GOD_SUPABASE_URL": "https://private.supabase.co",
        "SUPABASE_URL": "https://public.supabase.co",
    }

    with patch("agent.modes.get_mode_config") as mock_cfg:
        cfg = MagicMock()
        cfg.memory_db = str(db)
        mock_cfg.return_value = cfg

        with patch.object(Path, "exists", return_value=True), \
             patch.dict(os.environ, env):
            ok, reason = agent._check_god_mode_available()

    assert ok is True
    assert reason == ""
