"""testing/test_redaction.py — T-102: agent/redaction.py safe_error() contract."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.redaction import safe_error


# ── path redaction ────────────────────────────────────────────────────────────

def test_path_redaction_windows():
    e = FileNotFoundError(r"Cannot open e:\pi\.env config")
    result = safe_error(e)
    assert r"e:\pi" not in result
    assert "<path>" in result


def test_path_redaction_unix():
    e = FileNotFoundError("Cannot open /Users/ash/.env config")
    result = safe_error(e, audience="telegram")
    assert "/Users/ash" not in result
    assert "<path>" in result


# ── key redaction ─────────────────────────────────────────────────────────────

def test_key_redaction_openai():
    e = Exception("api call failed: sk-abc123def456ghi789jkl012mno345pqr678")
    result = safe_error(e)
    assert "sk-abc123" not in result
    assert "<key>" in result


def test_key_redaction_anthropic_jwt():
    e = Exception("invalid token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig")
    result = safe_error(e)
    assert "eyJhbGciOiJIUzI1NiI" not in result
    assert "<key>" in result


def test_key_redaction_groq():
    e = Exception("auth failed with gsk_testkey1234567890abcdefghijk")
    result = safe_error(e)
    assert "gsk_testkey" not in result
    assert "<key>" in result


def test_key_redaction_aws():
    e = Exception("invalid credential AKIAIOSFODNN7EXAMPLE")
    result = safe_error(e)
    assert "AKIAIOSFODNN7" not in result
    assert "<key>" in result


# ── traceback stripping ───────────────────────────────────────────────────────

def test_traceback_stripped():
    try:
        raise ValueError("inner error")
    except ValueError as inner:
        import traceback as tb
        raw = "".join(tb.format_exception(type(inner), inner, inner.__traceback__))
        e = RuntimeError(raw)
    result = safe_error(e)
    assert "Traceback" not in result
    assert "most recent call last" not in result


# ── edge cases ────────────────────────────────────────────────────────────────

def test_empty_exception_returns_type_name():
    result = safe_error(Exception())
    assert result == "Exception"


def test_audience_public_log_category_only():
    e = ValueError("something went wrong with /path/to/secret")
    result = safe_error(e, audience="public_log")
    assert result == "ValueError"
    assert "/path" not in result


def test_audience_telegram_length_cap():
    long_msg = "x" * 500
    e = RuntimeError(long_msg)
    result = safe_error(e, audience="telegram")
    assert len(result) <= 200


def test_audience_dev_unredacted():
    e = FileNotFoundError(r"missing e:\pi\.env and key sk-realkey1234567890abcdef")
    result = safe_error(e, audience="dev")
    assert r"e:\pi" in result or "sk-realkey" in result  # at least one preserved


def test_nested_cause_redacted():
    cause = FileNotFoundError("/Users/ash/secret.txt not found")
    try:
        raise RuntimeError("operation failed") from cause
    except RuntimeError as e:
        result = safe_error(e)
    assert "/Users/ash" not in result
    assert "<path>" in result
