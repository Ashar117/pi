"""testing/test_god_log_isolation.py — T-104: god-mode errors use public_log redaction."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_god_mode_error_uses_public_log_redaction():
    """safe_error with audience='public_log' must return only type name — no raw error body."""
    from agent.redaction import safe_error

    # Simulate what pi_agent.py now does for the evolution.jsonl error field
    raw_exc = RuntimeError(r"All providers failed: groq: e:\pi\secret path leaked")
    logged_error = safe_error(raw_exc, audience="public_log")

    # Must be just the type name — no path, no message body
    assert logged_error == "RuntimeError"
    assert r"e:\pi" not in logged_error
    assert "secret" not in logged_error


def test_public_log_audience_strips_key():
    from agent.redaction import safe_error
    exc = ValueError("auth failed sk-abc123def456ghi789jkl012mno345pqr678")
    result = safe_error(exc, audience="public_log")
    assert result == "ValueError"
    assert "sk-" not in result


def test_user_audience_redacts_but_preserves_context():
    from agent.redaction import safe_error
    exc = RuntimeError("connection to /var/run/pi.sock refused")
    result = safe_error(exc, audience="user")
    assert "/var/run" not in result
    assert "<path>" in result
    # message structure still present
    assert "connection" in result or "refused" in result or "<path>" in result
