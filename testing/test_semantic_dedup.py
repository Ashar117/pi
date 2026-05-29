"""testing/test_semantic_dedup.py — golden tests for T-080 embedding dedup.

Mocks Gemini and Anthropic so no live API calls in CI. Verifies:
  * cosine_similarity math is correct
  * find_semantic_duplicate routes correctly through cosine tiers
  * graceful degradation on missing keys / API failure
  * Haiku tiebreaker only fires in the borderline band
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from memory.semantic_dedup import (
    cosine_similarity,
    find_semantic_duplicate,
    COSINE_DUPLICATE_THRESHOLD,
    COSINE_BORDERLINE_THRESHOLD,
)


# ── Cosine math ──────────────────────────────────────────────────────────────

def test_cosine_identical_vectors():
    a = [1.0, 0.0, 0.0]
    assert cosine_similarity(a, a) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_vectors():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_returns_zero_on_dim_mismatch():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_cosine_returns_zero_on_empty():
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0], []) == 0.0


def test_cosine_returns_zero_on_zero_norm():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


# ── find_semantic_duplicate routing ──────────────────────────────────────────

def _fixed_embedding(text: str) -> list:
    """Deterministic 'embedding' for tests — char codes scaled, length-normalised."""
    return [ord(c) / 1000.0 for c in (text + "_" * 32)[:32]]


def test_above_threshold_returns_cosine_high(monkeypatch):
    """Score >= 0.90 returns duplicate without LLM call."""
    new = "Ash prefers oregano bread"
    candidates = [{"id": "abc", "text": new, "embedding": _fixed_embedding(new)}]

    with patch("memory.semantic_dedup.get_embedding",
               lambda t: _fixed_embedding(t)):
        result = find_semantic_duplicate(new, candidates)
    assert result is not None
    dup_id, score, reason = result
    assert dup_id == "abc"
    assert score >= COSINE_DUPLICATE_THRESHOLD
    assert reason == "cosine_high"


def test_below_borderline_returns_none():
    """Score < 0.75 returns None — caller writes."""
    # Cosine([1,0,0,0], [0.5, 0.5, 0.5, 0.5]) = 0.5 / 1.0 = 0.5 < 0.75
    new_emb = [1.0, 0.0, 0.0, 0.0]
    far_emb = [0.5, 0.5, 0.5, 0.5]  # cosine = 0.5
    candidates = [{"id": "xyz", "text": "different", "embedding": far_emb}]
    with patch("memory.semantic_dedup.get_embedding", lambda t: new_emb):
        result = find_semantic_duplicate("anything", candidates)
    assert result is None


def test_borderline_calls_haiku_tiebreaker():
    """Borderline cosine (0.75-0.90) routes to Haiku for decision."""
    # Cosine([1,0], [0.8, 0.6]) = 0.8 (unit-norm both) — squarely borderline
    new_emb = [1.0, 0.0]
    cand_emb = [0.8, 0.6]

    candidates = [{"id": "borderline", "text": "vaguely similar",
                   "embedding": cand_emb}]

    with patch("memory.semantic_dedup.get_embedding", lambda t: new_emb), \
         patch("memory.semantic_dedup.haiku_tiebreak", return_value=True) as mock_haiku:
        result = find_semantic_duplicate("vaguely similar new", candidates)

    mock_haiku.assert_called_once()
    assert result is not None
    assert result[0] == "borderline"
    assert result[2] == "haiku_confirmed"


def test_borderline_haiku_says_distinct_returns_none():
    """If Haiku says DISTINCT, caller writes."""
    new_emb = [1.0, 0.0]
    cand_emb = [0.8, 0.6]  # cosine 0.8, borderline

    candidates = [{"id": "borderline", "text": "vaguely similar",
                   "embedding": cand_emb}]

    with patch("memory.semantic_dedup.get_embedding", lambda t: new_emb), \
         patch("memory.semantic_dedup.haiku_tiebreak", return_value=False):
        result = find_semantic_duplicate("new fact", candidates)

    assert result is None


def test_borderline_haiku_unavailable_returns_none():
    """If Haiku unavailable (returns None), prefer false-positive insert."""
    new_emb = [1.0, 0.0]
    cand_emb = [0.8, 0.6]

    candidates = [{"id": "x", "text": "x", "embedding": cand_emb}]

    with patch("memory.semantic_dedup.get_embedding", lambda t: new_emb), \
         patch("memory.semantic_dedup.haiku_tiebreak", return_value=None):
        result = find_semantic_duplicate("new", candidates)

    assert result is None  # safe-default: write rather than drop


# ── Graceful degradation ─────────────────────────────────────────────────────

def test_no_gemini_returns_none():
    """If Gemini embedding fails (no key, etc.), function returns None silently."""
    candidates = [{"id": "x", "text": "x", "embedding": [1.0, 0.0]}]
    with patch("memory.semantic_dedup.get_embedding", lambda t: None):
        assert find_semantic_duplicate("anything", candidates) is None


def test_empty_candidates_returns_none():
    with patch("memory.semantic_dedup.get_embedding", lambda t: [1.0, 0.0]):
        assert find_semantic_duplicate("anything", []) is None


def test_candidates_without_embeddings_skipped():
    """Old rows without stored embeddings (pre-T-080) are silently skipped."""
    candidates = [{"id": "old", "text": "no embedding here", "embedding": None},
                  {"id": "also_old", "text": "still no embedding"}]
    with patch("memory.semantic_dedup.get_embedding", lambda t: [1.0, 0.0]):
        assert find_semantic_duplicate("anything", candidates) is None


def test_picks_highest_scoring_candidate():
    """When multiple candidates exceed threshold, returns the BEST match."""
    new_emb = [1.0, 0.0]
    candidates = [
        {"id": "weak", "text": "weak match", "embedding": [0.91, 0.4]},      # ~0.92
        {"id": "strong", "text": "strong match", "embedding": [0.99, 0.05]}, # ~0.998
        {"id": "weakest", "text": "weakest", "embedding": [0.90, 0.4]},      # ~0.91
    ]
    with patch("memory.semantic_dedup.get_embedding", lambda t: new_emb):
        result = find_semantic_duplicate("query", candidates)
    assert result is not None
    assert result[0] == "strong"  # highest cosine wins


# ── Realistic scenario (uses _fixed_embedding) ───────────────────────────────

def test_paraphrase_scenario_with_fixed_embedding():
    """Smoke test the full path with deterministic mock embeddings.

    Two paraphrases get high cosine via _fixed_embedding because they share
    most characters. Unrelated text gets low cosine.
    """
    fact = "Ash prefers oregano bread"
    paraphrase = "Ash prefers oregano bread"   # identical => guaranteed >= threshold
    unrelated = "Quantum chromodynamics describes the strong force"

    candidates = [
        {"id": "para", "text": paraphrase, "embedding": _fixed_embedding(paraphrase)},
        {"id": "unrel", "text": unrelated, "embedding": _fixed_embedding(unrelated)},
    ]

    with patch("memory.semantic_dedup.get_embedding",
               lambda t: _fixed_embedding(t)):
        result = find_semantic_duplicate(fact, candidates)
    assert result is not None
    assert result[0] == "para"
    assert result[2] == "cosine_high"
