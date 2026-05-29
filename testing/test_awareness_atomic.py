"""testing/test_awareness_atomic.py — T-106: awareness refresh spawns exactly one thread."""
import os
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_agent():
    from pi_agent import PiAgent
    agent = PiAgent.__new__(PiAgent)
    agent._awareness_snapshot_cache = "initial"
    agent._awareness_last_refresh = None
    agent._awareness_refresh_ttl = 0  # always stale
    agent._awareness_refreshing = False
    agent._awareness_refresh_failures = 0
    agent._awareness_refresh_lock = threading.Lock()
    agent.awareness = MagicMock()
    agent.awareness.get_awareness_snapshot.return_value = "refreshed"
    return agent


def test_awareness_single_refresh_under_contention():
    """10 concurrent threads hitting a stale TTL → get_awareness_snapshot called exactly once."""
    import time
    agent = _make_agent()
    # Slow down the snapshot so concurrent threads all enter before it completes
    call_event = threading.Event()

    def slow_snapshot(force=False):
        call_event.set()
        time.sleep(0.05)
        return "refreshed"

    agent.awareness.get_awareness_snapshot.side_effect = slow_snapshot

    barrier = threading.Barrier(10)
    errors = []

    def caller():
        try:
            barrier.wait()
            _ = agent.awareness_snapshot
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=caller) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # Wait for the refresh thread to complete
    time.sleep(0.2)

    assert errors == [], f"Thread errors: {errors}"
    call_count = agent.awareness.get_awareness_snapshot.call_count
    assert call_count == 1, f"Expected 1 refresh call, got {call_count}"


def test_awareness_flag_cleared_on_exception():
    """If _refresh raises, _awareness_refreshing must return to False."""
    agent = _make_agent()
    agent.awareness.get_awareness_snapshot.side_effect = RuntimeError("net err")

    # Trigger a refresh
    _ = agent.awareness_snapshot

    # Give the daemon thread time to complete
    import time
    time.sleep(0.2)

    assert agent._awareness_refreshing is False
