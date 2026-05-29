"""testing/test_salience.py — T-134: unit tests for memory/salience.py pure functions."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.salience import (
    composite_salience,
    recency_weight,
    affect_bonus,
    effective_importance,
    default_decay_rate,
    is_composite_mode,
    CATEGORY_DECAY_RATES,
)


class TestCompositeFormula:
    def test_all_defaults_gives_midrange_score(self):
        s = composite_salience()
        assert 0.2 < s < 0.8

    def test_high_importance_raises_score(self):
        low = composite_salience(importance=1)
        high = composite_salience(importance=10)
        assert high > low

    def test_urgent_affect_raises_score(self):
        neutral = composite_salience(affect_tag="neutral")
        urgent = composite_salience(affect_tag="urgent")
        assert urgent > neutral

    def test_all_max_inputs_approaches_one(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        s = composite_salience(
            importance=10, surprise_score=1.0, goal_alignment=1.0,
            created_at_iso=now_iso, affect_tag="neutral",
        )
        # 0.30*1 + 0.25*1 + 0.20*1 + 0.15*~1 + 0.10*0 = ~0.90
        assert 0.85 < s < 0.96

    def test_null_fields_fallback_no_exception(self):
        s = composite_salience(
            importance=None, surprise_score=None,
            goal_alignment=None, created_at_iso=None, affect_tag=None,
        )
        assert isinstance(s, float)
        assert 0.0 <= s <= 1.0

    def test_composite_higher_surprise_raises_score(self):
        low = composite_salience(surprise_score=0.0)
        high = composite_salience(surprise_score=1.0)
        assert high > low


class TestRecencyWeight:
    def test_brand_new_close_to_one(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        w = recency_weight(now_iso)
        assert w > 0.99

    def test_very_old_close_to_zero(self):
        old = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
        w = recency_weight(old)
        assert w < 0.001

    def test_half_life_at_30_days(self):
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        w = recency_weight(past, half_life_days=30.0)
        assert 0.45 < w < 0.55

    def test_none_returns_neutral(self):
        assert recency_weight(None) == 0.5

    def test_invalid_iso_returns_neutral(self):
        assert recency_weight("not-a-date") == 0.5

    def test_z_suffix_handled(self):
        iso_z = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        w = recency_weight(iso_z)
        assert w > 0.99


class TestAffectBonus:
    def test_neutral_zero(self):
        assert affect_bonus("neutral") == 0.0

    def test_urgent_highest(self):
        assert affect_bonus("urgent") == 0.8

    def test_important_mid(self):
        assert affect_bonus("important") == 0.5

    def test_joyful_positive(self):
        assert affect_bonus("joyful") == 0.3

    def test_painful_positive(self):
        assert affect_bonus("painful") == 0.4

    def test_unknown_tag_zero(self):
        assert affect_bonus("nonexistent_tag") == 0.0

    def test_case_insensitive(self):
        assert affect_bonus("URGENT") == 0.8

    def test_none_returns_zero(self):
        assert affect_bonus(None) == 0.0


class TestEffectiveImportance:
    def test_decay_reduces_over_time(self):
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        eff = effective_importance(importance=8, decay_rate=0.01, last_accessed_iso=old)
        # 8 * exp(-0.01*100) = 8 * exp(-1) ≈ 2.94
        assert eff < 4.0

    def test_pinned_immune_to_decay(self):
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        eff = effective_importance(importance=8, decay_rate=0.01,
                                   last_accessed_iso=old, pinned=1)
        assert eff == 8.0

    def test_no_decay_rate_returns_raw(self):
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        eff = effective_importance(importance=5, decay_rate=None, last_accessed_iso=old)
        assert eff == 5.0

    def test_no_access_time_returns_raw(self):
        eff = effective_importance(importance=7, decay_rate=0.01, last_accessed_iso=None)
        assert eff == 7.0

    def test_recent_access_minimal_decay(self):
        now = datetime.now(timezone.utc).isoformat()
        eff = effective_importance(importance=10, decay_rate=0.01, last_accessed_iso=now)
        assert eff > 9.9

    def test_null_importance_defaults_to_five(self):
        now = datetime.now(timezone.utc).isoformat()
        eff = effective_importance(importance=None, decay_rate=0.0, last_accessed_iso=now)
        assert eff == 5.0


class TestDefaultDecayRates:
    def test_permanent_profile_low_rate(self):
        r = default_decay_rate("permanent_profile")
        assert r == CATEGORY_DECAY_RATES["permanent_profile"]
        assert r < 0.005

    def test_session_history_fast_rate(self):
        r = default_decay_rate("session_history")
        assert r == CATEGORY_DECAY_RATES["session_history"]
        assert r >= 0.03

    def test_active_project_mid_rate(self):
        r = default_decay_rate("active_project")
        assert 0.005 < r < 0.05

    def test_unknown_category_default(self):
        r = default_decay_rate("made_up_category")
        assert r == 0.01

    def test_none_category_default(self):
        r = default_decay_rate(None)
        assert r == 0.01


class TestEnvFlag:
    def test_legacy_by_default(self, monkeypatch):
        monkeypatch.delenv("PI_SALIENCE_MODE", raising=False)
        assert not is_composite_mode()

    def test_composite_when_set(self, monkeypatch):
        monkeypatch.setenv("PI_SALIENCE_MODE", "composite")
        assert is_composite_mode()

    def test_legacy_when_explicitly_set(self, monkeypatch):
        monkeypatch.setenv("PI_SALIENCE_MODE", "legacy")
        assert not is_composite_mode()

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("PI_SALIENCE_MODE", "COMPOSITE")
        assert is_composite_mode()
