"""T-095 — God namespace cross-device Supabase wiring tests."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch


def _make_agent_with_god_env(god_url="", god_key="", supabase_url="https://public.supabase.co"):
    """Bootstrap a minimal PiAgent-like object for testing the god memory factory."""
    with patch.dict(os.environ, {
        "GOD_SUPABASE_URL": god_url,
        "GOD_SUPABASE_KEY": god_key,
        "SUPABASE_URL": supabase_url,
        "SUPABASE_KEY": "public-key",
    }):
        # Reload config so env changes are picked up
        import importlib
        import app.config as cfg_mod
        importlib.reload(cfg_mod)
        return cfg_mod


def test_god_supabase_url_and_key_exported():
    """GOD_SUPABASE_URL and GOD_SUPABASE_KEY must be declared in app.config."""
    import app.config as cfg
    assert hasattr(cfg, "GOD_SUPABASE_URL")
    assert hasattr(cfg, "GOD_SUPABASE_KEY")


def test_god_url_defaults_to_empty():
    """When env var is absent, GOD_SUPABASE_URL defaults to empty string."""
    env = {k: v for k, v in os.environ.items() if "GOD_SUPABASE" not in k}
    with patch.dict(os.environ, env, clear=True):
        import importlib
        import app.config as cfg
        importlib.reload(cfg)
        assert cfg.GOD_SUPABASE_URL == ""
        assert cfg.GOD_SUPABASE_KEY == ""


def test_fail_loud_when_god_url_equals_public_url():
    """Pi must raise when GOD_SUPABASE_URL == SUPABASE_URL (privacy regression guard)."""
    from agent.modes import ModeConfig, get_mode_config
    from tools.tools_memory import MemoryTools

    public_url = "https://public.supabase.co"

    fake_cfg = MagicMock()
    fake_cfg.memory_namespace = "god"
    fake_cfg.memory_db = "data/god_memory.db"

    mock_memory = MagicMock(spec=MemoryTools)

    # Simulate _get_memory_for_config logic inline
    with patch("pi_agent.GOD_SUPABASE_URL", public_url), \
         patch("pi_agent.GOD_SUPABASE_KEY", "some-key"), \
         patch("pi_agent.SUPABASE_URL", public_url):
        import pi_agent as pa
        agent = MagicMock()
        agent._memory_by_namespace = {}
        agent.memory = mock_memory

        with pytest.raises(RuntimeError, match="GOD_SUPABASE_URL must not equal SUPABASE_URL"):
            pa.PiAgent._get_memory_for_config(agent, fake_cfg)


def test_god_uses_private_supabase_when_creds_set():
    """When GOD_SUPABASE_URL differs from SUPABASE_URL, MemoryTools is built with god creds."""
    from tools.tools_memory import MemoryTools

    fake_cfg = MagicMock()
    fake_cfg.memory_namespace = "god"
    fake_cfg.memory_db = "data/god_memory.db"

    with patch("pi_agent.GOD_SUPABASE_URL", "https://private.supabase.co"), \
         patch("pi_agent.GOD_SUPABASE_KEY", "private-key"), \
         patch("pi_agent.SUPABASE_URL", "https://public.supabase.co"), \
         patch("tools.tools_memory.MemoryTools.__init__", return_value=None) as mock_init:
        import pi_agent as pa
        agent = MagicMock()
        agent._memory_by_namespace = {}
        agent.memory = MagicMock()

        pa.PiAgent._get_memory_for_config(agent, fake_cfg)

        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args[1]
        assert call_kwargs.get("supabase_url") == "https://private.supabase.co"
        assert call_kwargs.get("supabase_key") == "private-key"


def test_god_falls_back_to_local_when_no_creds():
    """When GOD_SUPABASE_URL is absent, god MemoryTools gets empty URL (noop Supabase)."""
    from tools.tools_memory import MemoryTools

    fake_cfg = MagicMock()
    fake_cfg.memory_namespace = "god"
    fake_cfg.memory_db = "data/god_memory.db"

    with patch("pi_agent.GOD_SUPABASE_URL", ""), \
         patch("pi_agent.GOD_SUPABASE_KEY", ""), \
         patch("pi_agent.SUPABASE_URL", "https://public.supabase.co"), \
         patch("tools.tools_memory.MemoryTools.__init__", return_value=None) as mock_init:
        import pi_agent as pa
        agent = MagicMock()
        agent._memory_by_namespace = {}
        agent.memory = MagicMock()

        pa.PiAgent._get_memory_for_config(agent, fake_cfg)

        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args[1]
        assert call_kwargs.get("supabase_url") == ""
        assert call_kwargs.get("supabase_key") == ""
