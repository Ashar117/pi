"""Tests for T-173: AwarenessCache (agent/awareness_cache.py)."""
from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.awareness_cache import AwarenessCache


def _make_tools(snapshot="<awareness>test</awareness>"):
    m = MagicMock()
    m.get_awareness_snapshot.return_value = snapshot
    return m


# ── first load ─────────────────────────────────────────────────────────────────

def test_first_snapshot_calls_tools():
    tools = _make_tools()
    cache = AwarenessCache(tools)
    result = cache.snapshot
    assert result == "<awareness>test</awareness>"
    tools.get_awareness_snapshot.assert_called_once()


def test_first_load_failure_returns_empty_string():
    tools = _make_tools()
    tools.get_awareness_snapshot.side_effect = RuntimeError("no internet")
    cache = AwarenessCache(tools)
    assert cache.snapshot == ""


def test_first_load_failure_does_not_raise():
    tools = _make_tools()
    tools.get_awareness_snapshot.side_effect = OSError("timeout")
    cache = AwarenessCache(tools)
    cache.snapshot  # must not raise


# ── caching ────────────────────────────────────────────────────────────────────

def test_second_call_returns_cached_without_tools_call():
    tools = _make_tools()
    cache = AwarenessCache(tools)
    _ = cache.snapshot
    _ = cache.snapshot
    assert tools.get_awareness_snapshot.call_count == 1


def test_ttl_not_expired_no_bg_refresh():
    tools = _make_tools()
    cache = AwarenessCache(tools, ttl=9999)
    _ = cache.snapshot  # first load
    _ = cache.snapshot  # should NOT trigger bg refresh
    time.sleep(0.05)
    assert tools.get_awareness_snapshot.call_count == 1


# ── setter ─────────────────────────────────────────────────────────────────────

def test_setter_overwrites_cache():
    tools = _make_tools()
    cache = AwarenessCache(tools)
    _ = cache.snapshot
    cache.snapshot = "overwritten"
    assert cache.snapshot == "overwritten"


def test_setter_updates_last_refresh():
    from datetime import datetime, timezone
    tools = _make_tools()
    cache = AwarenessCache(tools)
    _ = cache.snapshot
    cache.snapshot = "new"
    assert cache._last_refresh is not None


# ── background refresh ─────────────────────────────────────────────────────────

def test_ttl_expired_triggers_bg_refresh():
    tools = _make_tools()
    cache = AwarenessCache(tools, ttl=0)  # always expired
    _ = cache.snapshot  # first load
    tools.get_awareness_snapshot.return_value = "<awareness>fresh</awareness>"
    _ = cache.snapshot  # should kick off bg refresh
    time.sleep(0.1)
    assert cache._cache == "<awareness>fresh</awareness>"


def test_bg_refresh_failure_does_not_corrupt_cache():
    tools = _make_tools()
    cache = AwarenessCache(tools, ttl=0)
    _ = cache.snapshot  # first load, cache = "<awareness>test</awareness>"
    tools.get_awareness_snapshot.side_effect = RuntimeError("API down")
    _ = cache.snapshot  # triggers bg refresh that will fail
    time.sleep(0.1)
    # Cache should still hold the old value
    assert cache._cache == "<awareness>test</awareness>"


def test_only_one_bg_refresh_thread_spawned():
    """Two rapid calls with expired TTL spawn only one background thread."""
    tools = _make_tools()
    cache = AwarenessCache(tools, ttl=0)
    _ = cache.snapshot  # first load
    # Slow the bg thread so it's still "in flight" on the second call
    tools.get_awareness_snapshot.side_effect = lambda force=False: (time.sleep(0.1), "<r>")[1]
    _ = cache.snapshot  # spawns bg thread
    assert cache._refreshing is True
    _ = cache.snapshot  # second call — must not spawn another
    time.sleep(0.15)
    assert tools.get_awareness_snapshot.call_count == 2  # 1 first-load + 1 bg


# ── PiAgent delegation ─────────────────────────────────────────────────────────

def test_pi_agent_has_awareness_cache_attribute():
    """PiAgent must hold _awareness_cache, not the old six _awareness_* attrs."""
    import ast
    from pathlib import Path
    src = Path("pi_agent.py").read_text(encoding="utf-8")
    assert "_awareness_cache" in src
    assert "_awareness_snapshot_cache" not in src
    assert "_awareness_refresh_ttl" not in src


def test_awareness_snapshot_property_delegates():
    """awareness_snapshot property on PiAgent delegates to _awareness_cache.snapshot."""
    import ast
    from pathlib import Path
    src = Path("pi_agent.py").read_text(encoding="utf-8")
    assert "_awareness_cache.snapshot" in src
