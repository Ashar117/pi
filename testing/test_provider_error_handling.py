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
#
# T-084 (R3): _respond_normie now goes through self.router.chat(tier='cheap')
# instead of self.groq.chat.completions.create. When every provider in the
# tier is browned out / fails, the router raises RuntimeError with concatenated
# provider errors. _respond_normie classifies the error string ('rate'/'429'
# → rate_limit; 'api'/'status' → api_error; else → unknown) and returns the
# friendly message. Tests now mock router.chat to raise the right RuntimeError.


def _router_runtime_error(provider_err: str = "groq: rate limit reached (429)") -> RuntimeError:
    """Build the RuntimeError shape LLMRouter.chat raises when all providers fail."""
    return RuntimeError(f"All LLM providers failed or browned out.\n{provider_err}")


def test_429_does_not_leak_raw_error(normie_agent):
    """All providers 429 must not leak raw exception strings to the user."""
    with patch.object(normie_agent.router, "chat",
                      side_effect=_router_runtime_error("groq: 429 rate_limit_exceeded")):
        response = normie_agent.process_input("what time is it")

    leaked = _raw_error_leaked(response)
    assert leaked is None, (
        f"Raw provider error leaked to user (matched {leaked!r}):\n{response!r}"
    )


def test_429_gives_friendly_message(normie_agent):
    """All providers 429 should produce a helpful user-facing string."""
    with patch.object(normie_agent.router, "chat",
                      side_effect=_router_runtime_error("groq: 429 rate limit reached")):
        response = normie_agent.process_input("what time is it")

    assert _is_friendly(response), (
        f"Response to 429 was not user-friendly:\n{response!r}"
    )


def test_429_logs_failure_to_evolution(normie_agent):
    """All providers 429 must be logged to evolution.jsonl with success=False."""
    import os
    from pathlib import Path

    logs_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "logs"
    evolution_log = logs_dir / "evolution.jsonl"

    with patch.object(normie_agent.router, "chat",
                      side_effect=_router_runtime_error("groq: 429 rate_limit_exceeded")):
        normie_agent.process_input("test 429 logging")

    assert evolution_log.exists(), "evolution.jsonl does not exist"
    lines = [l for l in evolution_log.read_text().splitlines() if l.strip()]
    assert lines, "evolution.jsonl is empty after a router error — nothing was logged"
    last = json.loads(lines[-1])
    assert last.get("success") is False, (
        f"Evolution log entry after router-429 has success={last.get('success')!r}, expected False"
    )


# ── generic server error ──────────────────────────────────────────────────────

def test_500_does_not_leak_raw_error(normie_agent):
    """A generic provider 500 must not show raw exception text to user."""
    with patch.object(normie_agent.router, "chat",
                      side_effect=_router_runtime_error("groq: 500 internal server error")):
        response = normie_agent.process_input("hello")

    leaked = _raw_error_leaked(response)
    assert leaked is None, (
        f"Raw server error leaked to user (matched {leaked!r}):\n{response!r}"
    )
