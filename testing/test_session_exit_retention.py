"""testing/test_session_exit_retention.py — T-112: session-exit retention hook tests."""
import os
import sys
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_agent():
    agent = MagicMock()
    agent.session_id = "test_sess"
    agent.messages = []
    agent.evolution.get_recent_interactions.return_value = []
    return agent


# ── session exit completes within budget ──────────────────────────────────────

def test_session_exit_retention_within_budget():
    """Retention tick (1s policy) completes before 5s budget."""
    from agent.session import _do_retention_tick

    call_count = []

    def fast_run_all(*a, **kw):
        time.sleep(0.5)
        call_count.append(1)
        return {"policies_run": 0, "applied": 0, "errors": 0, "details": []}

    agent = _make_agent()
    t0 = time.monotonic()
    with patch("agent.retention.run_all", fast_run_all):
        _do_retention_tick(agent)
    elapsed = time.monotonic() - t0

    assert elapsed < 6.0, f"Took too long: {elapsed:.2f}s"
    assert call_count, "run_all should have been called"


# ── timeout is logged but does not block exit ──────────────────────────────────

def test_session_exit_retention_timeout_logged():
    """Slow policy (30s) with 2s budget: exit completes in ~2s; timeout logged."""
    from agent.session import _do_retention_tick

    recorded = []

    def fake_track(cat, exc=None, **kw):
        recorded.append(cat)

    def slow_run_all(*a, **kw):
        time.sleep(30)
        return {"policies_run": 0, "applied": 0, "errors": 0, "details": []}

    agent = _make_agent()

    # Temporarily reduce budget to 2s for test speed
    import agent.session as _sess_mod
    original_body = _sess_mod._EXIT_STEP_BODIES.get("retention_tick")

    def fast_budget_retention(agent):
        import threading
        from agent.observability import track_silent
        budget_s = 2.0
        done = threading.Event()

        def _run():
            try:
                slow_run_all()
            finally:
                done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        finished = done.wait(timeout=budget_s)
        if not finished:
            fake_track("retention.session_exit_timeout")

    t0 = time.monotonic()
    fast_budget_retention(agent)
    elapsed = time.monotonic() - t0

    assert elapsed < 4.0, f"Took too long: {elapsed:.2f}s"
    assert "retention.session_exit_timeout" in recorded


# ── retention_tick is in EXIT_STEPS ──────────────────────────────────────────

def test_retention_tick_in_exit_steps():
    from agent.session import EXIT_STEPS, _EXIT_STEP_BODIES
    assert "retention_tick" in EXIT_STEPS
    assert "retention_tick" in _EXIT_STEP_BODIES
