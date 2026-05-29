"""
testing/test_normie_provider.py — T-094

Asserts that normie mode dispatches through the LLMRouter cheap tier with
Cerebras as the first provider in the preference order.

These tests are unit-level (mock the network); the live Cerebras check
lives in scripts/sanity_check_normie.py.
"""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import List


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_mock_resp(provider: str = "cerebras") -> MagicMock:
    resp = MagicMock()
    resp.text = "ready"
    resp.provider = provider
    resp.model = f"{provider}-model"
    resp.stop_reason = "end_turn"
    resp.tokens_in = 10
    resp.tokens_out = 1
    resp.tool_calls = []
    return resp


# ── tests ─────────────────────────────────────────────────────────────────────

def test_cheap_tier_prefers_cerebras():
    """LLMRouter cheap tier lists Cerebras before Groq."""
    from core.llm_router import LLMRouter
    order = LLMRouter._TIER_ORDERS["cheap"]
    assert order[0] == "cerebras", (
        f"Expected cerebras first in cheap tier, got: {order[0]}"
    )
    assert "groq" in order, "Groq should be in cheap tier as fallback"


def test_normie_config_uses_cheap_tier():
    """ModeConfig for normie specifies router_tier='cheap'."""
    from agent.modes import get_mode_config
    cfg = get_mode_config("normie")
    assert cfg.router_tier == "cheap", (
        f"normie router_tier should be 'cheap', got '{cfg.router_tier}'"
    )


def test_normie_config_no_tools():
    """Normie ModeConfig has supports_tools=False (cheap providers don't support tools)."""
    from agent.modes import get_mode_config
    cfg = get_mode_config("normie")
    assert not cfg.supports_tools
    assert cfg.tool_allowlist == ()


def test_normie_dispatches_cerebras_first(monkeypatch):
    """When Cerebras is available, normie turn uses it (mock network call)."""
    from core.llm_router import LLMRouter

    cerebras_resp = _make_mock_resp("cerebras")
    call_log: List[str] = []

    original_providers_for_tier = LLMRouter._providers_for_tier

    def fake_providers_for_tier(self, tier):
        providers = original_providers_for_tier(self, tier)
        return providers

    router = LLMRouter(
        anthropic_key="",
        groq_key="fake-groq",
        gemini_key="",
        cerebras_key="fake-cerebras",
        openrouter_key="",
    )

    # Patch each provider's chat to record which was called
    for p in router._providers:
        name = p.name

        def make_chat(pname, resp):
            def chat(messages, system, tools, max_tokens):
                call_log.append(pname)
                if pname == "cerebras":
                    return resp
                raise RuntimeError(f"{pname} should not be called when cerebras succeeds")
            return chat

        p.chat = make_chat(name, cerebras_resp if name == "cerebras" else None)

    resp = router.chat(
        messages=[{"role": "user", "content": "hey"}],
        system="you are pi",
        tools=[],
        max_tokens=16,
        tier="cheap",
    )

    assert resp.provider == "cerebras", f"Expected cerebras, got {resp.provider}"
    assert call_log[0] == "cerebras", f"Cerebras should be called first, got {call_log}"


def test_normie_falls_back_to_groq_when_cerebras_fails(monkeypatch):
    """When Cerebras raises, normie falls back to Groq."""
    from core.llm_router import LLMRouter

    groq_resp = _make_mock_resp("groq")

    router = LLMRouter(
        anthropic_key="",
        groq_key="fake-groq",
        gemini_key="",
        cerebras_key="fake-cerebras",
        openrouter_key="",
    )

    for p in router._providers:
        name = p.name
        if name == "cerebras":
            def cerebras_chat(messages, system, tools, max_tokens):
                raise RuntimeError("cerebras timeout")
            p.chat = cerebras_chat
        elif name == "groq":
            def groq_chat(messages, system, tools, max_tokens):
                return groq_resp
            p.chat = groq_chat
        else:
            def other_chat(messages, system, tools, max_tokens):
                raise RuntimeError(f"{name} should not be called")
            p.chat = other_chat

    resp = router.chat(
        messages=[{"role": "user", "content": "hey"}],
        system="you are pi",
        tools=[],
        max_tokens=16,
        tier="cheap",
    )

    assert resp.provider == "groq", f"Expected groq fallback, got {resp.provider}"
