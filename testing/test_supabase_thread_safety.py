"""testing/test_supabase_thread_safety.py — T-105: _supa_lock guards MemoryTools."""
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_memory(tmp_path):
    from tools.tools_memory import MemoryTools
    db = str(tmp_path / "test.db")
    return MemoryTools(
        supabase_url="http://fake",
        supabase_key="fake",
        sqlite_path=db,
    )


# ── Lock exists and is RLock ──────────────────────────────────────────────────

def test_supa_lock_is_rlock(tmp_path):
    mem = _make_memory(tmp_path)
    assert hasattr(mem, "_supa_lock")
    # RLock can be acquired twice from the same thread
    mem._supa_lock.acquire()
    mem._supa_lock.acquire()  # would deadlock with plain Lock
    mem._supa_lock.release()
    mem._supa_lock.release()


# ── Concurrent inserts — no exception ────────────────────────────────────────

def test_concurrent_inserts(tmp_path):
    """10 threads each insert 10 entries; all succeed, no exception."""
    from tools.tools_memory import MemoryTools
    db = str(tmp_path / "test.db")
    mem = MemoryTools("", "", sqlite_path=db)  # _NoopSupabase (empty creds)

    errors = []

    def worker(i):
        try:
            for j in range(10):
                mem.memory_write(
                    content=f"fact {i}-{j}",
                    tier="l3",
                    importance=5,
                    session_id="test",
                )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Threads raised: {errors}"


# ── Lock released on exception ────────────────────────────────────────────────

def test_lock_released_on_exception(tmp_path):
    """If supabase call raises, lock must be released so subsequent call can acquire it."""
    mem = _make_memory(tmp_path)

    # Make supabase property return an object that raises
    bad_supa = MagicMock()
    bad_supa.table.return_value.insert.return_value.execute.side_effect = RuntimeError("boom")

    with patch.object(type(mem), "supabase", property(lambda self: bad_supa)):
        try:
            mem.memory_write(content="x", tier="l3", importance=5, session_id="s")
        except Exception:
            pass

    # Lock must be free — acquire without blocking
    acquired = mem._supa_lock.acquire(timeout=1.0)
    assert acquired, "Lock was not released after exception"
    mem._supa_lock.release()


# ── Re-entrant calls don't deadlock ──────────────────────────────────────────

def test_reentrant_calls(tmp_path):
    """A method holding _supa_lock can call another method that also takes it."""
    mem = _make_memory(tmp_path)

    def outer():
        with mem._supa_lock:
            # Inner call also takes the lock — RLock permits this
            with mem._supa_lock:
                return "ok"

    assert outer() == "ok"


# ── Read-write concurrency ────────────────────────────────────────────────────

def test_concurrent_read_write(tmp_path):
    """5 readers and 5 writers run simultaneously without raising."""
    from tools.tools_memory import MemoryTools
    db = str(tmp_path / "test.db")
    mem = MemoryTools("", "", sqlite_path=db)

    errors = []

    def reader():
        try:
            for _ in range(20):
                mem.memory_read(query="fact", tier="l3")
        except Exception as exc:
            errors.append(exc)

    def writer(i):
        try:
            for j in range(20):
                mem.memory_write(content=f"w{i}-{j}", tier="l3", importance=5, session_id="s")
        except Exception as exc:
            errors.append(exc)

    threads = (
        [threading.Thread(target=reader) for _ in range(5)]
        + [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Threads raised: {errors}"
