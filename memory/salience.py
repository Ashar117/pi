"""memory/salience.py — T-134: multi-dimensional salience scoring.

Replaces the single 'importance' int with a composite score. The retrieval
order in _hybrid_search_l3 uses composite_salience() when PI_SALIENCE_MODE=composite.

Formula:
    salience = 0.30 * importance_norm
             + 0.25 * surprise_score
             + 0.20 * goal_alignment
             + 0.15 * recency_weight
             + 0.10 * affect_bonus

All inputs normalised to [0, 1]. NULL fields fall back to neutral defaults so
existing rows without scores still work (backward-compatible).

ENV: PI_SALIENCE_MODE=composite  (default: legacy — no change in ordering)
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_ENV_FLAG = "PI_SALIENCE_MODE"

# Affect tag → bonus weight
_AFFECT_BONUS: Dict[str, float] = {
    "neutral": 0.0,
    "important": 0.5,
    "urgent": 0.8,
    "joyful": 0.3,
    "painful": 0.4,
}

# Per-category default decay rates (days⁻¹) — used by T-135
CATEGORY_DECAY_RATES: Dict[str, float] = {
    "permanent_profile": 0.002,
    "preferences": 0.005,
    "active_project": 0.01,
    "research_results": 0.008,
    "current_priority": 0.01,
    "session_history": 0.05,
    "note": 0.01,
    "bulk_test": 0.1,
    "test": 0.1,
    "integration_test": 0.1,
}
_DEFAULT_DECAY_RATE = 0.01


def is_composite_mode() -> bool:
    return os.environ.get(_ENV_FLAG, "legacy").lower() == "composite"


def recency_weight(created_at_iso: Optional[str], half_life_days: float = 30.0) -> float:
    """Exponential decay from created_at. Returns 1.0 for brand-new, ~0 for very old."""
    if not created_at_iso:
        return 0.5
    try:
        created = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - created).total_seconds() / 86400
        return math.exp(-days_old * math.log(2) / half_life_days)
    except Exception:
        return 0.5


def affect_bonus(tag: Optional[str]) -> float:
    """Map affect_tag string to a [0, 1] bonus."""
    return _AFFECT_BONUS.get((tag or "neutral").lower(), 0.0)


def composite_salience(
    importance: Optional[int] = None,
    surprise_score: Optional[float] = None,
    goal_alignment: Optional[float] = None,
    created_at_iso: Optional[str] = None,
    affect_tag: Optional[str] = None,
) -> float:
    """Compute composite salience score.

    Any NULL field receives a neutral default so existing rows without scores
    produce a meaningful result. Pure function — easy to unit-test.
    """
    imp_norm = (importance or 5) / 10.0
    surprise = surprise_score if surprise_score is not None else 0.5
    goal = goal_alignment if goal_alignment is not None else 0.5
    recency = recency_weight(created_at_iso)
    affect = affect_bonus(affect_tag)

    return (
        0.30 * imp_norm
        + 0.25 * surprise
        + 0.20 * goal
        + 0.15 * recency
        + 0.10 * affect
    )


def default_decay_rate(category: Optional[str]) -> float:
    """Return per-category decay rate (days⁻¹). Used at L3 write time."""
    return CATEGORY_DECAY_RATES.get(category or "", _DEFAULT_DECAY_RATE)


def effective_importance(
    importance: Optional[int],
    decay_rate: Optional[float],
    last_accessed_iso: Optional[str],
    pinned: int = 0,
) -> float:
    """T-135: importance * exp(-decay_rate * days_since_access).

    Pinned rows (pinned=1) are immune — always return raw importance.
    Falls back to raw importance on any error (Inv. 9).
    """
    raw = float(importance or 5)
    if pinned:
        return raw
    if not decay_rate or not last_accessed_iso:
        return raw
    try:
        last = datetime.fromisoformat(last_accessed_iso.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        return raw * math.exp(-decay_rate * days)
    except Exception:
        return raw
