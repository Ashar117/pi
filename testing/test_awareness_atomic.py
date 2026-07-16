"""testing/test_awareness_atomic.py — T-106: awareness refresh spawns exactly one thread.

Updated for T-173: logic moved to AwarenessCache; tests now exercise the cache directly.
"""
import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.awareness_cache import AwarenessCache


def _make_cache(initial="initial", ttl=0):
    """Build an AwarenessCache with a pre-loaded snapshot and expired TTL."""
    tools = MagicMock()
    tools.get_awareness_snapshot.return_value = initial
    cache = AwarenessCache(tools, ttl=ttl)
    # Warm it — first load so cache is populated, then reset call count
    _ = cache.snapshot
    tools.get_awareness_snapshot.reset_mock()
    tools.get_awareness_snapshot.return_value = "refreshed"
    return cache, tools


def test_awareness_single_refresh_under_contention():
    """10 concurrent threads hitting a stale TTL → get_awareness_snapshot called exactly once for bg refresh."""
    cache, tools = _make_cache()

    call_event = threading.Event()

    def slow_snapshot(force=False):
        call_event.set()
        time.sleep(0.05)
        return "refreshed"

    tools.get_awareness_snapshot.side_effect = slow_snapshot

    barrier = threading.Barrier(10)
    errors = []

    def caller():
        try:
            barrier.wait()
            _ = cache.snapshot
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=caller) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    time.sleep(0.2)

    assert errors == [], f"Thread errors: {errors}"
    # The first load already called once; only 1 more bg call expected
    assert tools.get_awareness_snapshot.call_count == 1, (
        f"Expected 1 bg refresh call, got {tools.get_awareness_snapshot.call_count}"
    )


def test_awareness_flag_cleared_on_exception():
    """If _refresh raises, _refreshing must return to False."""
    cache, tools = _make_cache()
    tools.get_awareness_snapshot.side_effect = RuntimeError("net err")

    # Trigger a bg refresh (TTL=0 so any call after first-load triggers it)
    _ = cache.snapshot

    time.sleep(0.2)

    assert cache._refreshing is False
