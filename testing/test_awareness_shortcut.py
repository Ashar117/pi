"""
testing/test_awareness_shortcut.py — T-030: awareness snapshot must be queryable
directly without an LLM call for weather, markets, and news questions.

Evidence: normie mode returned rate-limit error on "what's the weather" because
it called Groq rather than reading the snapshot already loaded at startup.
Root mode answered correctly only because Claude happened to notice the snapshot
in the system prompt — not because any structured shortcut existed.

Offline — no API calls, no agent startup.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Fixtures ──────────────────────────────────────────────────────────────────

_WEATHER_SNAPSHOT = (
    "=== LIVE AWARENESS ===\n"
    "Time: Sunday, 2026-05-04 14:00 UTC\n"
    "Weather: Springfield — Partly Cloudy, 22°C/72°F  "
    "Humidity 58%  Wind 12 km/h  UV 4  [via open-meteo.com]\n"
    "Markets: AAPL $189.50 (+1.2%)  |  NVDA $875.00 (-0.3%)\n"
    "\nGlobal News [via BBC RSS]:\n"
    "  • Scientists discover new exoplanet\n"
    "  • G7 summit concludes with trade deal\n"
    "\nHacker News Top Stories:\n"
    "  • Show HN: I built a self-hosted RSS reader  [342pts | 88c]\n"
    "\n=== END AWARENESS ==="
)

_EMPTY_SNAPSHOT = ""
_UNAVAILABLE_SNAPSHOT = (
    "=== LIVE AWARENESS ===\n"
    "Weather: unavailable\n"
    "Markets: unavailable\n"
    "=== END AWARENESS ==="
)


# ── Import test: function must exist ──────────────────────────────────────────

def test_try_answer_from_awareness_importable():
    """try_answer_from_awareness must exist in agent.awareness_shortcut."""
    from agent.awareness_shortcut import try_answer_from_awareness
    assert callable(try_answer_from_awareness)


# ── Weather questions ─────────────────────────────────────────────────────────

def test_weather_question_returns_snapshot_data():
    """'what's the weather' must return snapshot weather line, not None."""
    from agent.awareness_shortcut import try_answer_from_awareness
    result = try_answer_from_awareness("what's the weather like?", _WEATHER_SNAPSHOT)
    assert result is not None, "Expected snapshot answer, got None"
    assert "22" in result or "72" in result or "Cloudy" in result, (
        f"Response doesn't contain weather data: {result!r}"
    )


def test_weather_unavailable_returns_none():
    """If snapshot says 'unavailable', shortcut must return None (let LLM handle)."""
    from agent.awareness_shortcut import try_answer_from_awareness
    result = try_answer_from_awareness("what's the weather?", _UNAVAILABLE_SNAPSHOT)
    assert result is None, (
        f"Expected None when weather is unavailable, got: {result!r}"
    )


def test_empty_snapshot_returns_none():
    """Empty snapshot must always return None."""
    from agent.awareness_shortcut import try_answer_from_awareness
    result = try_answer_from_awareness("what's the weather?", _EMPTY_SNAPSHOT)
    assert result is None


# ── Markets questions ─────────────────────────────────────────────────────────

def test_markets_question_returns_snapshot_data():
    """'how are stocks doing' must return the Markets line from snapshot."""
    from agent.awareness_shortcut import try_answer_from_awareness
    for q in ("how are stocks doing?", "what's the market like today?", "AAPL price?"):
        result = try_answer_from_awareness(q, _WEATHER_SNAPSHOT)
        assert result is not None, f"Expected snapshot answer for {q!r}, got None"
        assert "AAPL" in result or "NVDA" in result or "Market" in result.title(), (
            f"Markets response missing ticker data: {result!r}"
        )


# ── News questions ────────────────────────────────────────────────────────────

def test_news_question_returns_headlines():
    """'what's in the news' must return headlines from the snapshot."""
    from agent.awareness_shortcut import try_answer_from_awareness
    result = try_answer_from_awareness("what's in the news today?", _WEATHER_SNAPSHOT)
    assert result is not None, "Expected snapshot headlines, got None"
    assert "exoplanet" in result or "G7" in result or "BBC" in result, (
        f"News response missing headlines: {result!r}"
    )


# ── Non-awareness questions must pass through ─────────────────────────────────

def test_non_awareness_question_returns_none():
    """Questions unrelated to awareness data must return None."""
    from agent.awareness_shortcut import try_answer_from_awareness
    unrelated = [
        "what is the meaning of life?",
        "help me write a cover letter",
        "explain gradient descent",
        "what did I tell you about my project?",
        "hi",
    ]
    for q in unrelated:
        result = try_answer_from_awareness(q, _WEATHER_SNAPSHOT)
        assert result is None, (
            f"Non-awareness question {q!r} should return None, got: {result!r}"
        )
