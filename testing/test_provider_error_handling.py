"""
testing/test_provider_error_handling.py — T-025: raw provider errors must not
reach the user as response text.

Evidence from real chat session:
    Pi: [Pi] Groq error: Error code: 429 - {'error': {'message': 'Rate limit
        reached for model `llama-3.3-70b-versatile`...', 'type': 'tokens', ...}}

All Groq calls are mocked — no network, no API key required.
"""
import sys
import os
import json
import re
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── phrases that prove the raw error leaked ───────────────────────────────────

RAW_ERROR_PATTERNS = [
    r"Error code:",
    r"'message':",           # JSON dict fragment
    r"\[Pi\] Groq error:",   # the internal tag
    r"rate_limit_exceeded",  # raw API error code
    r"tokens per day \(TPD\)",
    r"https://console\.groq\.com",
]

# ── phrases that prove a friendly response ────────────────────────────────────

FRIENDLY_INDICATORS = [
    "limit",
    "root mode",
    "try again",
    "moment",
    "unavailable",
    "wait",
    "later",
]


def _raw_error_leaked(text: str) -> str | None:
    for pat in RAW_ERROR_PATTERNS:
        if re.search(pat, text):
            return pat
    return None


def _is_friendly(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in FRIENDLY_INDICATORS)


def _make_groq_429():
    """Return an exception that looks like a Groq 429 rate-limit error."""
    from groq import RateLimitError
    # RateLimitError needs response + body; fake them
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    body = {
        "error": {
            "message": (
                "Rate limit reached for model `llama-3.3-70b-versatile` "
                "on tokens per day (TPD): Limit 100000, Used 99999. "
                "Please try again in 1h. Need more tokens? Upgrade at "
                "https://console.groq.com/settings/billing"
            ),
            "type": "tokens",
            "code": "rate_limit_exceeded",
        }
    }
    return RateLimitError(
        message="Rate limit exceeded",
        response=mock_response,
        body=body,
    )


def _make_groq_500():
    """Return a generic Groq API error (server-side)."""
    from groq import InternalServerError
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.headers = {}
    return InternalServerError(
        message="Internal server error",
        response=mock_response,
        body={"error": {"message": "Internal error", "type": "server_error"}},
    )


@pytest.fixture(scope="module")
def normie_agent():
    def fake_input(prompt=""):
        return "n"

    with patch("builtins.input", side_effect=fake_input):
        from pi_agent import PiAgent
        agent = PiAgent()
        agent.mode = "normie"
    return agent


# ── 429 rate-limit ────────────────────────────────────────────────────────────

def test_429_does_not_leak_raw_error(normie_agent):
    """A Groq 429 must not show the raw exception string to the user."""
    exc = _make_groq_429()
    with patch.object(normie_agent.groq.chat.completions, "create", side_effect=exc):
        response = normie_agent.process_input("what time is it")

    leaked = _raw_error_leaked(response)
    assert leaked is None, (
        f"Raw provider error leaked to user (matched {leaked!r}):\n{response!r}"
    )


def test_429_gives_friendly_message(normie_agent):
    """A Groq 429 should produce a helpful user-facing string."""
    exc = _make_groq_429()
    with patch.object(normie_agent.groq.chat.completions, "create", side_effect=exc):
        response = normie_agent.process_input("what time is it")

    assert _is_friendly(response), (
        f"Response to 429 was not user-friendly:\n{response!r}"
    )


def test_429_logs_failure_to_evolution(normie_agent):
    """A Groq 429 must be logged to evolution.jsonl with success=False."""
    import os
    from pathlib import Path

    logs_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "logs"
    evolution_log = logs_dir / "evolution.jsonl"

    exc = _make_groq_429()
    with patch.object(normie_agent.groq.chat.completions, "create", side_effect=exc):
        normie_agent.process_input("test 429 logging")

    assert evolution_log.exists(), "evolution.jsonl does not exist"
    lines = [l for l in evolution_log.read_text().splitlines() if l.strip()]
    assert lines, "evolution.jsonl is empty after a Groq error — nothing was logged"
    last = json.loads(lines[-1])
    assert last.get("success") is False, (
        f"Evolution log entry after 429 has success={last.get('success')!r}, expected False"
    )


# ── generic server error ──────────────────────────────────────────────────────

def test_500_does_not_leak_raw_error(normie_agent):
    """A generic Groq server error must not show raw exception text to user."""
    exc = _make_groq_500()
    with patch.object(normie_agent.groq.chat.completions, "create", side_effect=exc):
        response = normie_agent.process_input("hello")

    leaked = _raw_error_leaked(response)
    assert leaked is None, (
        f"Raw server error leaked to user (matched {leaked!r}):\n{response!r}"
    )
