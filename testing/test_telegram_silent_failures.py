"""T-254: instrumented Telegram silent-failure sites report to track_silent.

Full handler-closure coverage (guest approve/deny notify, login/profile
delete-message) needs the fake-bot harness T-261 is scoped to build; this
covers what's directly callable today: _store_media_to_memory.
"""
import os
import sys
import sqlite3
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    import agent.observability as obs
    test_db = tmp_path / "silent_failures.db"
    with (
        patch.object(obs, "_DB_PATH", test_db),
        patch.object(obs, "_conn", None),
        patch.object(obs, "_insert_count", 0),
    ):
        yield test_db
    obs._conn = None


class _FailingMemory:
    def memory_write(self, **kwargs):
        raise RuntimeError("memory backend unavailable")


class _FakeAgent:
    def __init__(self):
        self.memory = _FailingMemory()


def test_store_media_to_memory_failure_is_tracked(fresh_db):
    from tools.tools_telegram import TelegramTools
    tt = TelegramTools(agent=_FakeAgent(), use_bubble=False)

    # Must not raise even though memory_write blows up.
    tt._store_media_to_memory("a photo of a cat", "photo", filename="cat.jpg")

    conn = sqlite3.connect(str(fresh_db))
    rows = conn.execute(
        "SELECT category, exception_type FROM silent_failures"
    ).fetchall()
    conn.close()
    assert rows == [("telegram.store_media_to_memory", "RuntimeError")]


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    import agent.observability as obs
    with tempfile.TemporaryDirectory() as d:
        test_db = Path(d) / "silent_failures.db"
        obs._DB_PATH = test_db
        obs._conn = None
        obs._insert_count = 0
        test_store_media_to_memory_failure_is_tracked(test_db)
    print("OK")
