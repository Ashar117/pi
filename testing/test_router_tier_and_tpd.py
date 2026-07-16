"""testing/test_router_tier_and_tpd.py — T-084 R3 acceptance tests.

Covers:
  1. Tier matrix returns correct provider ordering per tier
  2. 'default' aliases to 'balanced'
  3. Unknown tier falls through to full provider list (safety net)
  4. CostTracker.record() stores the tier column
  5. CostTracker.tokens_today(provider) aggregates correctly
  6. LLMRouter._is_browned_out fires on TPD saturation
  7. Saturated provider in tier='cheap' routes to next provider

All mocked — no network, no real API keys.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ── 1-3: tier matrix ─────────────────────────────────────────────────────────

@pytest.fixture
def full_router():
    """Router with all providers stubbed (fake keys are enough — no calls fire)."""
    from core.llm_router import LLMRouter
    return LLMRouter(
        anthropic_key="k", groq_key="k", gemini_key="k",
        cerebras_key="k", openrouter_key="k",
    )


def test_tier_matrix_orderings(full_router):
    """Each tier returns providers in the documented order."""
    cases = {
        "premium":  ["anthropic", "gemini"],
        "balanced": ["anthropic", "groq", "gemini", "cerebras", "openrouter"],
        "cheap":    ["cerebras", "groq", "gemini", "openrouter"],
        "fast":     ["cerebras", "groq"],
    }
    for tier, expected in cases.items():
        actual = [p.name for p in full_router._providers_for_tier(tier)]
        assert actual == expected, f"tier={tier}: expected {expected}, got {actual}"


def test_default_aliases_to_balanced(full_router):
    """tier='default' must produce the same ordering as tier='balanced'."""
    assert [p.name for p in full_router._providers_for_tier("default")] == \
           [p.name for p in full_router._providers_for_tier("balanced")]


def test_unknown_tier_falls_through(full_router):
    """Unknown tier returns the full provider list (safety net)."""
    actual = [p.name for p in full_router._providers_for_tier("nonsense")]
    full = [p.name for p in full_router._providers]
    assert actual == full


# ── 4-5: CostTracker tier column + tokens_today ─────────────────────────────

@pytest.fixture
def temp_cost_tracker():
    """Fresh CostTracker bound to a temp DB so tests don't pollute the real one.

    `ignore_cleanup_errors=True` is needed on Windows where SQLite occasionally
    holds the file open across the yield boundary (WAL journal lock); the test
    body has already finished by then so the cleanup race is cosmetic.
    """
    from core.cost_tracker import CostTracker
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        ct = CostTracker(db_path=Path(td) / "test_cost.db")
        yield ct


def test_record_stores_tier(temp_cost_tracker):
    """record(tier=...) persists tier in the llm_costs.tier column."""
    import sqlite3
    temp_cost_tracker.record(
        provider="groq", model="llama-3.3-70b-versatile",
        tokens_in=100, tokens_out=50, session_id="abc", tier="cheap",
    )
    with sqlite3.connect(str(temp_cost_tracker._path)) as conn:
        row = conn.execute(
            "SELECT provider, model, tier FROM llm_costs ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    assert row is not None, "no row inserted"
    assert row[2] == "cheap", f"tier column: expected 'cheap', got {row[2]!r}"


def test_record_default_tier_is_balanced(temp_cost_tracker):
    """record() without explicit tier defaults to 'balanced'."""
    import sqlite3
    temp_cost_tracker.record(
        provider="anthropic", model="claude-sonnet-4-6",
        tokens_in=10, tokens_out=5,
    )
    with sqlite3.connect(str(temp_cost_tracker._path)) as conn:
        row = conn.execute("SELECT tier FROM llm_costs ORDER BY ts DESC LIMIT 1").fetchone()
    assert row[0] == "balanced"


def test_tokens_today_aggregates(temp_cost_tracker):
    """tokens_today(provider) returns SUM(tokens_in + tokens_out) for today."""
    temp_cost_tracker.record("groq", "llama-3.3-70b-versatile", 1000, 500, tier="cheap")
    temp_cost_tracker.record("groq", "llama-3.3-70b-versatile", 200, 100, tier="cheap")
    temp_cost_tracker.record("cerebras", "llama-3.3-70b", 50, 25, tier="cheap")
    assert temp_cost_tracker.tokens_today("groq") == 1800
    assert temp_cost_tracker.tokens_today("cerebras") == 75
    assert temp_cost_tracker.tokens_today("nonexistent") == 0


# ── 6-7: TPD brownout fires + routes around saturated provider ──────────────

def test_tpd_brownout_fires_at_threshold(full_router, temp_cost_tracker):
    """_is_browned_out returns True when tokens_today/budget > 0.9."""
    # Wire the temp cost tracker into the router
    full_router._cost = temp_cost_tracker
    # Default groq budget is 100_000. Record 95_000 used → 95% → browned out.
    temp_cost_tracker.record("groq", "llama-3.3-70b-versatile",
                             tokens_in=50_000, tokens_out=45_000, tier="cheap")
    assert full_router._is_browned_out("groq") is True

    # cerebras with 1M budget, only 100k used (10%) → NOT browned out.
    temp_cost_tracker.record("cerebras", "llama-3.3-70b",
                             tokens_in=60_000, tokens_out=40_000, tier="cheap")
    assert full_router._is_browned_out("cerebras") is False


def test_tpd_brownout_routes_to_next_provider(full_router, temp_cost_tracker):
    """When the tier's first provider is TPD-browned, chat() falls through."""
    full_router._cost = temp_cost_tracker
    # Saturate cerebras: tier='cheap' starts with cerebras → needs to skip to groq.
    temp_cost_tracker.record("cerebras", "llama-3.3-70b",
                             tokens_in=950_000, tokens_out=60_000, tier="cheap")
    assert full_router._is_browned_out("cerebras") is True

    # Mock groq provider chat to capture that it was called (the fallthrough target).
    groq_provider = next(p for p in full_router._providers if p.name == "groq")
    from core.llm_router import LLMResponse
    groq_provider.chat = MagicMock(return_value=LLMResponse(
        text="ok from groq", provider="groq", model="llama-3.3-70b-versatile",
        tokens_in=10, tokens_out=5, stop_reason="end_turn",
    ))

    # Also stub cerebras.chat so even if it WERE tried we wouldn't hit network.
    cerebras_provider = next(p for p in full_router._providers if p.name == "cerebras")
    cerebras_provider.chat = MagicMock(side_effect=AssertionError(
        "cerebras was called despite being TPD-browned out"
    ))

    resp = full_router.chat(
        messages=[{"role": "user", "content": "hello"}],
        system="", tools=[], max_tokens=100, tier="cheap",
    )
    assert resp.provider == "groq"
    assert resp.text == "ok from groq"
    cerebras_provider.chat.assert_not_called()
    groq_provider.chat.assert_called_once()


def test_no_budget_means_no_tpd_brownout(full_router, temp_cost_tracker):
    """Providers with no daily budget (anthropic, ollama) never TPD-brown out."""
    full_router._cost = temp_cost_tracker
    # Pretend anthropic burned 10M tokens — no budget defined → no brownout.
    temp_cost_tracker.record("anthropic", "claude-sonnet-4-6",
                             tokens_in=5_000_000, tokens_out=5_000_000, tier="premium")
    assert full_router._is_browned_out("anthropic") is False
