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


# ── T-160: self-location shortcut ──────────────────────────────────────────────

_LOCATION_SNAPSHOT = (
    "=== LIVE AWARENESS ===\n"
    "Time: Sunday, 2026-05-04 14:00 UTC  |  Atlanta, Georgia, US\n"
    "Location: Atlanta, Georgia, US (approximate, from network)\n"
    "Weather: Atlanta — Clear, 20°C/68°F  Humidity 50%  Wind 8 km/h  UV 3  [via wttr]\n"
    "=== END AWARENESS ==="
)


def test_where_am_i_returns_live_location():
    from agent.awareness_shortcut import try_answer_from_awareness
    out = try_answer_from_awareness("where am i right now?", _LOCATION_SNAPSHOT)
    assert out is not None and "Atlanta" in out


def test_exact_location_returns_live_location():
    from agent.awareness_shortcut import try_answer_from_awareness
    out = try_answer_from_awareness("hey what's my exact location?", _LOCATION_SNAPSHOT)
    assert out is not None and "Atlanta" in out


def test_location_answer_is_consistent_across_phrasings():
    """The bug was contradictory answers to near-identical questions. Deterministic now."""
    from agent.awareness_shortcut import try_answer_from_awareness
    a = try_answer_from_awareness("what's my exact location?", _LOCATION_SNAPSHOT)
    b = try_answer_from_awareness("where am i right now?", _LOCATION_SNAPSHOT)
    assert a == b and a is not None


def test_location_unavailable_falls_through():
    from agent.awareness_shortcut import try_answer_from_awareness
    snap = "=== LIVE AWARENESS ===\nTime: x\nWeather: unavailable\n=== END ==="
    assert try_answer_from_awareness("where am i right now?", snap) is None


def test_nearby_place_question_not_treated_as_self_location():
    from agent.awareness_shortcut import try_answer_from_awareness
    # 'where is the nearest coffee shop' is NOT a self-location query → no shortcut
    out = try_answer_from_awareness("where is the nearest coffee shop", _LOCATION_SNAPSHOT)
    assert out is None


# ── T-211: imperative commands must NOT be hijacked by the shortcut ────────────
# Regression for the live bug: "Add the ticket" (autocorrect "ticker") returned
# cached BTC/ETH prices instead of creating a ticket, because "ticker" is a market
# signal word and the shortcut had no intent gate.

def test_action_command_with_market_word_falls_through():
    """Imperative commands containing a market word must return None (→ LLM/tools)."""
    from agent.awareness_shortcut import try_answer_from_awareness
    commands = [
        "add the ticker",
        "Add the ticket",
        "create a ticket for the market bug",
        "buy NVDA",
        "track BTC",
        "set a price alert on AAPL",
        "watch the market",
        "hey can you add the ticker",
    ]
    for c in commands:
        assert try_answer_from_awareness(c, _WEATHER_SNAPSHOT) is None, (
            f"Command {c!r} was hijacked by the shortcut — expected None"
        )


def test_questions_still_return_snapshot_after_intent_gate():
    """The intent gate must NOT break genuine awareness questions."""
    from agent.awareness_shortcut import try_answer_from_awareness
    questions = [
        "what's the market like today?",
        "AAPL price?",
        "how are stocks doing?",
        "tell me the news",
        "what's the weather",
    ]
    for q in questions:
        assert try_answer_from_awareness(q, _WEATHER_SNAPSHOT) is not None, (
            f"Question {q!r} should still return snapshot data, got None"
        )


# ── Markets specificity guard: out-of-scope instruments must NOT hijack ────────
# Regression for the live nightmare: "for futures trading and shyt, whats up with
# wheat and soybean" returned the canned "Markets: BTC/ETH" line because "trading"
# is a market signal and _extract_markets had no query-scope check.

_CRYPTO_MARKET_SNAPSHOT = (
    "=== LIVE AWARENESS ===\n"
    "Time: Saturday, 2026-07-11 14:00 UTC\n"
    "Markets: BTC-USD $64,243.00 (+0.3%)  |  ETH-USD $1,812.69 (+1.1%)\n"
    "=== END AWARENESS ==="
)


def test_commodity_futures_query_falls_through_to_llm():
    """Wheat/soybean/commodity/forex/bond queries must return None (→ LLM+tools)."""
    from agent.awareness_shortcut import try_answer_from_awareness
    out_of_scope = [
        "for futures trading and shyt, whats up with wheat and soybean?",
        "how's the wheat market doing",
        "soybean futures today?",
        "what's the price of gold and silver",
        "crude oil price?",
        "how are treasury yields",
        "eur usd forex rate?",
    ]
    for q in out_of_scope:
        assert try_answer_from_awareness(q, _CRYPTO_MARKET_SNAPSHOT) is None, (
            f"Out-of-scope market query {q!r} was hijacked by the shortcut — expected None"
        )


def test_crypto_specific_query_fires_with_crypto_snapshot():
    """Crypto-specific questions must still fire against a crypto-only snapshot."""
    from agent.awareness_shortcut import try_answer_from_awareness
    crypto_qs = [
        "btc price?",
        "how's bitcoin doing",
        "what's ethereum at",
        "how's the crypto market",
    ]
    for q in crypto_qs:
        out = try_answer_from_awareness(q, _CRYPTO_MARKET_SNAPSHOT)
        assert out is not None and "BTC" in out, (
            f"Crypto query {q!r} should return the snapshot line, got {out!r}"
        )


def test_general_market_query_declines_when_snapshot_is_crypto_only():
    """'how's the market' / 'how are stocks' must NOT be served a crypto-only
    line — that is the 'i didnt ask for bitcoin' bug. Defer to LLM+tools."""
    from agent.awareness_shortcut import try_answer_from_awareness
    equity_qs = [
        "how's the market these days?",
        "how are stocks doing",
        "hows the stock market",
        "what's the nasdaq at",
    ]
    for q in equity_qs:
        out = try_answer_from_awareness(q, _CRYPTO_MARKET_SNAPSHOT)
        assert out is None, (
            f"Equity/general query {q!r} against a crypto-only snapshot should decline, got {out!r}"
        )


def test_general_market_query_fires_when_snapshot_has_equities():
    """When the snapshot carries equities, 'how's the market' fires normally."""
    from agent.awareness_shortcut import try_answer_from_awareness
    for q in ("how's the market today", "how are stocks doing"):
        out = try_answer_from_awareness(q, _WEATHER_SNAPSHOT)  # has AAPL/NVDA
        assert out is not None and ("AAPL" in out or "NVDA" in out), (
            f"Market query {q!r} should fire when equities are present, got {out!r}"
        )


def test_root_mode_config_disables_shortcut():
    """T-211: root must not run the awareness shortcut (it has tools; must act)."""
    from agent.modes import get_mode_config
    assert get_mode_config("root").awareness_shortcut is False
    # normie keeps the shortcut for its no-tools fast path
    assert get_mode_config("normie").awareness_shortcut is True
