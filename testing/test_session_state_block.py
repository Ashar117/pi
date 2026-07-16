"""Tests for T-194: CURRENT SESSION STATE block in dynamic prompt segment."""
import pytest
from unittest.mock import MagicMock, patch
from agent.prompt import build_session_state_block
from agent.modes import MODE_CONFIGS, ModeConfig


# ── build_session_state_block unit tests ──────────────────────────────────────

def test_block_contains_mode_name():
    cfg = MODE_CONFIGS["root"]
    block = build_session_state_block(cfg, "abc123", 5, 64)
    assert "ROOT" in block


def test_block_contains_conversation_id():
    cfg = MODE_CONFIGS["normie"]
    block = build_session_state_block(cfg, "deadbeef", 3, 32)
    assert "deadbeef" in block


def test_block_contains_turn_number():
    cfg = MODE_CONFIGS["root"]
    block = build_session_state_block(cfg, "x", 7, 10)
    assert "7" in block


def test_block_contains_tool_count():
    cfg = MODE_CONFIGS["normie"]
    block = build_session_state_block(cfg, "y", 1, 42)
    assert "42" in block


def test_block_includes_refusal_hint_when_set():
    cfg = MODE_CONFIGS["normie"]
    assert cfg.refusal_hint, "normie must have a refusal_hint for this test to mean anything"
    block = build_session_state_block(cfg, "z", 2, 30)
    assert cfg.refusal_hint in block


def test_block_is_string():
    cfg = MODE_CONFIGS["root"]
    block = build_session_state_block(cfg, "abc", 1, 5)
    assert isinstance(block, str)
    assert len(block) > 0


# ── ModeConfig.refusal_hint field tests ───────────────────────────────────────

def test_root_mode_has_refusal_hint():
    assert MODE_CONFIGS["root"].refusal_hint != ""


def test_normie_mode_has_refusal_hint():
    assert MODE_CONFIGS["normie"].refusal_hint != ""


def test_refusal_hint_default_empty_for_custom_config():
    """Unset refusal_hint defaults to empty string."""
    cfg = ModeConfig(
        name="test",
        prompt_path="prompts/consciousness.txt",
        memory_db=None,
        memory_namespace="pi",
        vault_path="vault",
        tickets_dir="tickets",
        router_tier="default",
        supports_tools=False,
        tool_allowlist=(),
        max_tokens=1024,
        public_logging=False,
    )
    assert cfg.refusal_hint == ""


# ── Integration: block lands in dynamic segment, not static ──────────────────

def _make_mock_agent(mode="root"):
    """Build a minimal mock PiAgent for prompt-assembly tests."""
    from agent.modes import MODE_CONFIGS
    agent = MagicMock()
    agent.mode = mode
    agent.conversation_id = "test123"
    agent.turn_number = 4
    agent._normie_handoff_context = ""
    agent.awareness_snapshot = ""
    # Return a plausible (static, warm, dynamic) triple
    agent._get_system_prompt_split.return_value = ("STATIC", "WARM", "DYNAMIC_TIME")
    return agent


def test_static_segment_unchanged_with_block(tmp_path):
    """The static segment must not include the session state block."""
    from agent.prompt import build_system_prompt_split, build_session_state_block
    from agent.modes import MODE_CONFIGS

    cfg = MODE_CONFIGS["root"]

    class FakeMemory:
        def get_l3_context(self, max_tokens=300):
            return ""

    consciousness = "You are Pi."
    static, warm, dynamic = build_system_prompt_split(consciousness, "root", FakeMemory())
    block = build_session_state_block(cfg, "abc", 1, 64)

    # T-194 block's per-turn content (conversation_id, turn count) must NOT be in the
    # static segment — the static must be turn-invariant for prompt cache to work.
    assert "abc" not in static        # conversation_id not in static
    assert "TURN: 1" not in static    # turn number not in static
    assert "TOOLS AVAILABLE THIS TURN" not in static
