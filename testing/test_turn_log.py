"""
testing/test_turn_log.py — Phase A unit tests for the universal turn log (T-039).

Verifies every conversation turn — both modes, every return path — appends a
line to logs/turns.jsonl.

All offline. No real Claude/Groq calls; subsystems are stubbed.
"""

import json
import os
import sys
import tempfile
import builtins
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Suppress monthly-review prompt
_real_input = builtins.input
builtins.input = lambda *args, **kwargs: "no"


def _redirect_log(tmp_path):
    """Patch agent.turn_log to write to tmp_path instead of real logs/."""
    from agent import turn_log
    log_file = tmp_path / "turns.jsonl"
    return patch.object(turn_log, "_LOG_PATH", log_file), log_file


# ── append_turn directly ─────────────────────────────────────────────────────

class TestAppendTurn:
    def test_writes_one_line(self, tmp_path):
        from agent.turn_log import append_turn
        log_file = tmp_path / "turns.jsonl"
        with patch("agent.turn_log._LOG_PATH", log_file):
            tid = append_turn(
                session_id="abc",
                mode="normie",
                user_input="hello",
                response="hi back",
                duration_ms=42,
            )
        assert tid is not None
        assert log_file.exists()
        lines = log_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["mode"] == "normie"
        assert entry["session_id"] == "abc"
        assert entry["user_input"] == "hello"
        assert entry["response_preview"] == "hi back"
        assert entry["duration_ms"] == 42

    def test_response_preview_truncated_at_400(self, tmp_path):
        from agent.turn_log import append_turn
        log_file = tmp_path / "turns.jsonl"
        with patch("agent.turn_log._LOG_PATH", log_file):
            append_turn(
                session_id="s", mode="root",
                user_input="x", response="y" * 1000, duration_ms=1,
            )
        entry = json.loads(log_file.read_text().splitlines()[0])
        assert len(entry["response_preview"]) == 400
        assert entry["response_chars"] == 1000

    def test_writes_error_field(self, tmp_path):
        from agent.turn_log import append_turn
        log_file = tmp_path / "turns.jsonl"
        with patch("agent.turn_log._LOG_PATH", log_file):
            append_turn(
                session_id="s", mode="root",
                user_input="break", response="", duration_ms=0,
                error="boom",
            )
        entry = json.loads(log_file.read_text().splitlines()[0])
        assert entry["error"] == "boom"

    def test_disk_failure_returns_none_no_raise(self, tmp_path):
        from agent.turn_log import append_turn
        bad = tmp_path / "nope" / "cant" / "write.jsonl"
        with patch("agent.turn_log._LOG_PATH", bad):
            # Make the parent's mkdir raise to simulate permission failure
            with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
                tid = append_turn(
                    session_id="s", mode="root",
                    user_input="x", response="y", duration_ms=0,
                )
        assert tid is None  # signals failure but does NOT raise


# ── count_today / recent_turns ───────────────────────────────────────────────

class TestQueries:
    def test_count_today_empty(self, tmp_path):
        from agent.turn_log import count_today
        with patch("agent.turn_log._LOG_PATH", tmp_path / "turns.jsonl"), \
             patch("agent.turn_log._COUNTS_DB", tmp_path / "turn_counts.db"):
            assert count_today() == 0

    def test_count_today_filters_by_session(self, tmp_path):
        from agent import turn_log
        log = tmp_path / "turns.jsonl"
        counts_db = tmp_path / "turn_counts.db"
        with patch.object(turn_log, "_LOG_PATH", log), \
             patch.object(turn_log, "_COUNTS_DB", counts_db):
            turn_log.append_turn(session_id="A", mode="normie",
                                 user_input="1", response="r", duration_ms=1)
            turn_log.append_turn(session_id="A", mode="normie",
                                 user_input="2", response="r", duration_ms=1)
            turn_log.append_turn(session_id="B", mode="root",
                                 user_input="3", response="r", duration_ms=1)
            assert turn_log.count_today() == 3
            assert turn_log.count_today(session_id="A") == 2
            assert turn_log.count_today(session_id="B") == 1

    def test_recent_turns_returns_newest_last(self, tmp_path):
        from agent import turn_log
        log = tmp_path / "turns.jsonl"
        with patch.object(turn_log, "_LOG_PATH", log):
            for i in range(5):
                turn_log.append_turn(session_id="x", mode="normie",
                                     user_input=f"q{i}", response=f"a{i}",
                                     duration_ms=1)
            recent = turn_log.recent_turns(limit=3)
            assert len(recent) == 3
            # newest last
            assert recent[-1]["user_input"] == "q4"
            assert recent[0]["user_input"] == "q2"


# ── process_input wrapper integration ────────────────────────────────────────

class TestProcessInputWrapper:
    """Verify the new process_input wrapper logs every return path."""

    def _agent(self):
        from pi_agent import PiAgent
        return PiAgent()

    def test_exit_command_logs_a_turn(self, tmp_path):
        from agent import turn_log
        log = tmp_path / "turns.jsonl"
        with patch.object(turn_log, "_LOG_PATH", log):
            a = self._agent()
            out = a.process_input("exit")
            assert out == "EXIT"
            assert log.exists()
            lines = log.read_text(encoding="utf-8").splitlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["user_input"] == "exit"
            assert entry["response_preview"] == "EXIT"

    def test_mode_switch_logs_a_turn(self, tmp_path):
        from agent import turn_log
        log = tmp_path / "turns.jsonl"
        with patch.object(turn_log, "_LOG_PATH", log):
            a = self._agent()
            out = a.process_input("root mode")
            assert a.mode == "root"
            entry = json.loads(log.read_text().splitlines()[0])
            assert entry["mode"] == "root"  # post-switch mode is captured
            assert "root" in entry["response_preview"].lower()

    def test_inner_exception_still_logs(self, tmp_path):
        """If _process_input_inner raises, the wrapper still writes a turn with error."""
        from agent import turn_log
        log = tmp_path / "turns.jsonl"
        with patch.object(turn_log, "_LOG_PATH", log):
            a = self._agent()
            with patch.object(a, "_process_input_inner",
                              side_effect=RuntimeError("kaboom")):
                out = a.process_input("anything")
            assert "kaboom" in out  # wrapper returns error message
            entry = json.loads(log.read_text().splitlines()[0])
            assert entry["error"] == "kaboom"


# ── T-259: turns.jsonl rotation ───────────────────────────────────────────────

class TestRotateTurnsLog:
    def test_below_threshold_is_a_noop(self, tmp_path):
        from agent import turn_log
        log = tmp_path / "turns.jsonl"
        log.write_text('{"turn_id": "1"}\n', encoding="utf-8")
        with patch.object(turn_log, "_LOG_PATH", log), \
             patch.object(turn_log, "_ARCHIVE_DIR", tmp_path / "archive"):
            result = turn_log.rotate_turns_log(threshold_bytes=1_000_000)
        assert result is None
        assert log.read_text(encoding="utf-8") == '{"turn_id": "1"}\n'

    def test_missing_file_is_a_noop(self, tmp_path):
        from agent import turn_log
        with patch.object(turn_log, "_LOG_PATH", tmp_path / "turns.jsonl"), \
             patch.object(turn_log, "_ARCHIVE_DIR", tmp_path / "archive"):
            assert turn_log.rotate_turns_log() is None

    def test_above_threshold_archives_and_truncates(self, tmp_path):
        from agent import turn_log
        log = tmp_path / "turns.jsonl"
        archive_dir = tmp_path / "archive"
        content = '{"turn_id": "1", "user_input": "hi"}\n{"turn_id": "2", "user_input": "there"}\n'
        log.write_text(content, encoding="utf-8")

        with patch.object(turn_log, "_LOG_PATH", log), \
             patch.object(turn_log, "_ARCHIVE_DIR", archive_dir):
            result = turn_log.rotate_turns_log(threshold_bytes=1)

        assert result is not None
        assert result.name.startswith("turns_jsonl-") and result.name.endswith(".jsonl.gz")
        assert log.read_text(encoding="utf-8") == ""  # truncated, not deleted

        # The reader recent_turns() already relies on must be able to parse it back.
        records = turn_log._read_gz_jsonl(result)
        assert [r["turn_id"] for r in records] == ["1", "2"]

    def test_recent_turns_finds_archived_records(self, tmp_path):
        """Integration: rotate, then confirm recent_turns() walks the archive."""
        from agent import turn_log
        log = tmp_path / "turns.jsonl"
        archive_dir = tmp_path / "archive"
        log.write_text('{"turn_id": "1", "session_id": "s1", "ts": "2026-01-01T00:00:00Z"}\n',
                       encoding="utf-8")

        with patch.object(turn_log, "_LOG_PATH", log), \
             patch.object(turn_log, "_ARCHIVE_DIR", archive_dir):
            turn_log.rotate_turns_log(threshold_bytes=1)
            results = turn_log.recent_turns(limit=5)

        assert any(r.get("turn_id") == "1" for r in results)


def teardown_module(module):
    builtins.input = _real_input
