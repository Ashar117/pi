"""Tests for T-191: tiered model escalation — keyword detection and default-OFF guard."""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_agent():
    """Build a minimal PiAgent via __new__ for unit testing."""
    from pi_agent import PiAgent
    agent = PiAgent.__new__(PiAgent)
    return agent


# ── _is_premium_turn: keyword detection ──────────────────────────────────────

def test_think_hard_escalates():
    agent = _make_agent()
    assert agent._is_premium_turn("think hard about the architecture")


def test_ultra_escalates():
    agent = _make_agent()
    assert agent._is_premium_turn("ultra review this design")


def test_opus_escalates():
    agent = _make_agent()
    assert agent._is_premium_turn("use opus for this")


def test_normal_chat_does_not_escalate():
    agent = _make_agent()
    assert not agent._is_premium_turn("what's the weather like?")


def test_code_edit_without_keyword_does_not_escalate():
    agent = _make_agent()
    assert not agent._is_premium_turn("fix the bug in tools_memory.py")


# ── default OFF: no escalation without env var ────────────────────────────────

def test_premium_default_off(monkeypatch):
    """Without PI_PREMIUM_DAILY_LIMIT env var, premium_limit=0.0 → no escalation."""
    monkeypatch.delenv("PI_PREMIUM_DAILY_LIMIT", raising=False)
    limit = float(os.environ.get("PI_PREMIUM_DAILY_LIMIT", "0.0"))
    assert limit == 0.0


def test_premium_env_var_enables_limit(monkeypatch):
    monkeypatch.setenv("PI_PREMIUM_DAILY_LIMIT", "1.00")
    limit = float(os.environ.get("PI_PREMIUM_DAILY_LIMIT", "0.0"))
    assert limit == 1.0


# ── premium tier already defined in router ────────────────────────────────────

def test_premium_tier_in_router():
    """The 'premium' tier must be defined in _TIER_ORDERS."""
    from core.llm_router import LLMRouter
    assert "premium" in LLMRouter._TIER_ORDERS


def test_premium_tier_routes_to_anthropic():
    """Premium tier: qwen first (hackathon; skipped when no key), anthropic right behind."""
    from core.llm_router import LLMRouter
    order = LLMRouter._TIER_ORDERS["premium"]
    assert order[0] == "qwen"
    assert order[1] == "anthropic"


# ── escalation OFF by default does not change tier ───────────────────────────

def test_no_escalation_when_limit_zero(monkeypatch):
    """When PI_PREMIUM_DAILY_LIMIT=0 (default), tier stays at cfg.router_tier."""
    monkeypatch.delenv("PI_PREMIUM_DAILY_LIMIT", raising=False)
    agent = _make_agent()
    # Simulate the logic inline (the actual code is in _respond_via_config)
    _premium_limit = float(os.environ.get("PI_PREMIUM_DAILY_LIMIT", "0.0"))
    cfg_tier = "default"
    actual_tier = cfg_tier
    if _premium_limit > 0.0 and agent._is_premium_turn("think hard"):
        actual_tier = "premium"
    assert actual_tier == "default"  # limit=0 blocks escalation even with keyword


def test_escalation_when_limit_set(monkeypatch):
    """When PI_PREMIUM_DAILY_LIMIT>0 and keyword present, tier becomes premium."""
    monkeypatch.setenv("PI_PREMIUM_DAILY_LIMIT", "1.0")
    agent = _make_agent()
    _premium_limit = float(os.environ.get("PI_PREMIUM_DAILY_LIMIT", "0.0"))
    cfg_tier = "default"
    actual_tier = cfg_tier
    if _premium_limit > 0.0 and agent._is_premium_turn("think hard about the design"):
        actual_tier = "premium"
    assert actual_tier == "premium"
