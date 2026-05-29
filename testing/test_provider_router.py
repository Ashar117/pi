"""testing/test_provider_router.py — T-121: provider chain + circuit breaker tests."""
import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_router(providers, **kwargs):
    from agent.provider_router import ProviderRouter
    return ProviderRouter(name="test", providers=providers, **kwargs)


# ── happy path ────────────────────────────────────────────────────────────────

def test_first_provider_success_no_fallback():
    calls = []
    def p1(*a, **kw):
        calls.append("p1")
        return "p1_result"
    def p2(*a, **kw):
        calls.append("p2")
        return "p2_result"

    r = _make_router([("p1", p1), ("p2", p2)])
    result = r.call("arg")
    assert result == "p1_result"
    assert calls == ["p1"]


# ── 429 advances chain ────────────────────────────────────────────────────────

def test_429_advances_to_next_provider():
    from agent.provider_router import RateLimitError
    def p1(*a, **kw):
        raise RateLimitError("p1", retry_after_s=30)
    def p2(*a, **kw):
        return "p2_ok"

    r = _make_router([("p1", p1), ("p2", p2)])
    assert r.call("x") == "p2_ok"


def test_implicit_429_detected_from_message():
    """Errors containing 'RESOURCE_EXHAUSTED' or '429' are treated as rate limits."""
    def p1(*a, **kw):
        raise RuntimeError("429 RESOURCE_EXHAUSTED — retry in 40s")
    def p2(*a, **kw):
        return "p2_ok"

    r = _make_router([("p1", p1), ("p2", p2)])
    assert r.call("x") == "p2_ok"
    # The 40s retry should be parsed
    assert r._cooldown_until["p1"] > time.time() + 35


# ── all providers exhausted ───────────────────────────────────────────────────

def test_all_fail_raises_exhausted():
    from agent.provider_router import AllProvidersExhausted, RateLimitError
    def p1(*a, **kw):
        raise RateLimitError("p1", retry_after_s=10)
    def p2(*a, **kw):
        raise RuntimeError("p2 totally broken")

    r = _make_router([("p1", p1), ("p2", p2)])
    with pytest.raises(AllProvidersExhausted) as exc_info:
        r.call("x")
    assert len(exc_info.value.chain_history) == 2
    assert "p1" in exc_info.value.chain_history[0][0]
    assert "p2" in exc_info.value.chain_history[1][0]


# ── circuit breaker opens after threshold ─────────────────────────────────────

def test_circuit_opens_after_threshold():
    from agent.provider_router import RateLimitError
    call_count = {"p1": 0}
    def p1(*a, **kw):
        call_count["p1"] += 1
        # retry_after_s=0 so cooldown ends instantly; this isolates the test
        # to circuit-breaker behaviour rather than per-call cooldown timing.
        raise RateLimitError("p1", retry_after_s=0)
    def p2(*a, **kw):
        return "p2_ok"

    r = _make_router([("p1", p1), ("p2", p2)], circuit_threshold=3, circuit_cooldown_s=300)

    # 3 calls — each should hit p1 then fall to p2
    for _ in range(3):
        r.call("x")
    assert call_count["p1"] == 3

    # 4th call: p1 should be in circuit-open cooldown (5 min); skipped entirely
    r.call("x")
    assert call_count["p1"] == 3  # not incremented — circuit was open


def test_success_resets_failure_counter():
    from agent.provider_router import RateLimitError
    state = {"fail_count": 0}
    def p1(*a, **kw):
        if state["fail_count"] < 2:
            state["fail_count"] += 1
            raise RateLimitError("p1", retry_after_s=0.01)  # very short cooldown
        return "p1_recovered"
    def p2(*a, **kw):
        return "p2_ok"

    r = _make_router([("p1", p1), ("p2", p2)], circuit_threshold=3)

    # 2 fails → p2 returns
    r.call("x"); time.sleep(0.02)
    r.call("x"); time.sleep(0.02)
    # 3rd: p1 returns; failures should reset
    assert r.call("x") == "p1_recovered"
    assert r._consecutive_failures["p1"] == 0


# ── retry-after parsing ───────────────────────────────────────────────────────

def test_retry_after_parsed_from_google_message():
    from agent.provider_router import parse_retry_after_google
    e = RuntimeError("429 RESOURCE_EXHAUSTED. Please retry in 40.698557649s.")
    assert parse_retry_after_google(e) == pytest.approx(40.698557649)


def test_retry_after_parsed_from_google_delay_field():
    from agent.provider_router import parse_retry_after_google
    e = RuntimeError("error: {'retryDelay': '40s'}")
    assert parse_retry_after_google(e) == 40.0


def test_retry_after_parsed_from_anthropic_header():
    from agent.provider_router import parse_retry_after_anthropic

    class FakeResponse:
        headers = {"retry-after": "75"}

    class FakeError(Exception):
        response = FakeResponse()

    assert parse_retry_after_anthropic(FakeError("boom")) == 75.0


# ── cooldown release ─────────────────────────────────────────────────────────

def test_provider_resumed_after_cooldown_expires():
    from agent.provider_router import RateLimitError
    fails = [True]
    def p1(*a, **kw):
        if fails[0]:
            fails[0] = False
            raise RateLimitError("p1", retry_after_s=0.05)
        return "p1_ok"

    r = _make_router([("p1", p1)], circuit_threshold=10)
    # First call: 429 → cooldown 0.05s
    from agent.provider_router import AllProvidersExhausted
    with pytest.raises(AllProvidersExhausted):
        r.call("x")
    # Wait for cooldown
    time.sleep(0.1)
    # Now p1 should be retried
    assert r.call("x") == "p1_ok"


# ── reset ─────────────────────────────────────────────────────────────────────

def test_reset_clears_state():
    from agent.provider_router import RateLimitError
    def p1(*a, **kw):
        raise RateLimitError("p1", retry_after_s=300)
    def p2(*a, **kw):
        return "ok"

    r = _make_router([("p1", p1), ("p2", p2)])
    r.call("x")
    assert "p1" in r._cooldown_until
    r.reset()
    assert r._cooldown_until == {}
    assert r._consecutive_failures == {}


# ── thread safety smoke test ──────────────────────────────────────────────────

def test_concurrent_calls_no_state_corruption():
    import threading
    from agent.provider_router import RateLimitError
    def p1(*a, **kw):
        raise RateLimitError("p1", retry_after_s=60)
    def p2(*a, **kw):
        return "p2_ok"

    r = _make_router([("p1", p1), ("p2", p2)])

    results = []
    def worker():
        for _ in range(10):
            results.append(r.call("x"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert all(r_ == "p2_ok" for r_ in results)
    assert len(results) == 40
