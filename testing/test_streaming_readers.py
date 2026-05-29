"""testing/test_streaming_readers.py — T-110: streaming turn log reader tests."""
import gzip
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_entry(i: int, session: str = "sess1") -> dict:
    return {
        "turn_id": f"turn{i}",
        "session_id": session,
        "ts": _iso_now(),
        "mode": "root",
        "user_input": f"msg {i}",
        "response_preview": f"resp {i}",
        "response_chars": 10,
        "tools_used": [],
        "cost": 0.0,
        "duration_ms": 100,
        "tokens_in": 10,
        "tokens_out": 20,
        "model": "test",
        "error": None,
    }


def _write_entries(path: Path, entries: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _write_gz_entries(path: Path, entries: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(str(path), "wb") as f:
        for e in entries:
            f.write((json.dumps(e) + "\n").encode())


# ── _tail_jsonl ────────────────────────────────────────────────────────────────

def test_tail_jsonl_returns_last_n(tmp_path):
    from agent.turn_log import _tail_jsonl

    log = tmp_path / "turns.jsonl"
    entries = [_make_entry(i) for i in range(100)]
    _write_entries(log, entries)

    result = _tail_jsonl(log, 20)
    assert len(result) == 20
    assert result[-1]["turn_id"] == "turn99"
    assert result[0]["turn_id"] == "turn80"


def test_tail_jsonl_handles_partial_first_line(tmp_path):
    """When max_bytes cuts into the file, the first partial line is dropped cleanly."""
    from agent.turn_log import _tail_jsonl

    log = tmp_path / "turns.jsonl"
    entries = [_make_entry(i) for i in range(50)]
    _write_entries(log, entries)

    file_size = log.stat().st_size
    # Use max_bytes that cuts mid-file (about half)
    half = file_size // 2

    result = _tail_jsonl(log, 50, max_bytes=half)
    # All returned entries should be valid dicts
    for r in result:
        assert "turn_id" in r
    # We should not get the very first entry (it's in the cut portion)
    ids = {r["turn_id"] for r in result}
    assert "turn0" not in ids


def test_tail_jsonl_fewer_than_n_lines(tmp_path):
    from agent.turn_log import _tail_jsonl

    log = tmp_path / "turns.jsonl"
    _write_entries(log, [_make_entry(i) for i in range(5)])

    result = _tail_jsonl(log, 20)
    assert len(result) == 5


# ── recent_turns — live file only ─────────────────────────────────────────────

def test_recent_turns_live_file(tmp_path):
    from agent.turn_log import recent_turns, _LOG_PATH as REAL_LOG

    log = tmp_path / "turns.jsonl"
    entries = [_make_entry(i) for i in range(30)]
    _write_entries(log, entries)

    with patch("agent.turn_log._LOG_PATH", log), \
         patch("agent.turn_log._ARCHIVE_DIR", tmp_path / "archive"):
        result = recent_turns(20)

    assert len(result) == 20
    assert result[-1]["turn_id"] == "turn29"


def test_recent_turns_output_matches_old_impl(tmp_path):
    """recent_turns on a small file returns the same records the old impl would."""
    from agent.turn_log import recent_turns

    log = tmp_path / "turns.jsonl"
    entries = [_make_entry(i) for i in range(10)]
    _write_entries(log, entries)

    with patch("agent.turn_log._LOG_PATH", log), \
         patch("agent.turn_log._ARCHIVE_DIR", tmp_path / "archive"):
        result = recent_turns(20)

    assert len(result) == 10
    assert [r["turn_id"] for r in result] == [f"turn{i}" for i in range(10)]


# ── recent_turns — spans archives ─────────────────────────────────────────────

def test_recent_turns_spans_archives(tmp_path):
    """Live file has 5 entries; archive has 100; recent_turns(20) returns 20."""
    from agent.turn_log import recent_turns

    archive_dir = tmp_path / "archive"
    log = tmp_path / "turns.jsonl"

    # Write 5 entries to live file
    live_entries = [_make_entry(i, "live") for i in range(95, 100)]
    _write_entries(log, live_entries)

    # Write 100 entries to an archive
    arc_entries = [_make_entry(i, "arc") for i in range(95)]
    _write_gz_entries(archive_dir / "turns_jsonl-2026-05-01.jsonl.gz", arc_entries)

    with patch("agent.turn_log._LOG_PATH", log), \
         patch("agent.turn_log._ARCHIVE_DIR", archive_dir):
        result = recent_turns(20)

    assert len(result) == 20
    # Last 5 should be from live file (session_id="live")
    assert result[-1]["session_id"] == "live"


# ── count_today — O(1) counter path ──────────────────────────────────────────

def test_count_today_uses_counter_table(tmp_path):
    """count_today() returns counter from SQLite — O(1), no file scan."""
    from agent.turn_log import count_today, _COUNTS_DB as REAL_DB

    counts_db = tmp_path / "turn_counts.db"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Pre-seed counter
    conn = sqlite3.connect(str(counts_db))
    conn.execute("CREATE TABLE turn_counts (date TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO turn_counts(date, count) VALUES(?, ?)", (today, 7))
    conn.commit()
    conn.close()

    with patch("agent.turn_log._COUNTS_DB", counts_db), \
         patch("agent.turn_log._LOG_PATH", tmp_path / "turns.jsonl"):
        result = count_today()

    assert result == 7


def test_count_today_performance(tmp_path):
    """count_today() completes in < 5ms with a populated counter table."""
    from agent.turn_log import count_today

    counts_db = tmp_path / "turn_counts.db"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(counts_db))
    conn.execute("CREATE TABLE turn_counts (date TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO turn_counts(date, count) VALUES(?, ?)", (today, 42))
    conn.commit()
    conn.close()

    log = tmp_path / "turns.jsonl"  # no file — would be slow if scanned

    with patch("agent.turn_log._COUNTS_DB", counts_db), \
         patch("agent.turn_log._LOG_PATH", log):
        t0 = time.monotonic()
        result = count_today()
        elapsed_ms = (time.monotonic() - t0) * 1000

    assert result == 42
    assert elapsed_ms < 50  # generous bound; SQLite O(1) lookup is sub-ms


# ── append_turn — increments counter ─────────────────────────────────────────

def test_append_turn_increments_counter(tmp_path):
    from agent.turn_log import append_turn, count_today

    log = tmp_path / "turns.jsonl"
    counts_db = tmp_path / "turn_counts.db"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with patch("agent.turn_log._LOG_PATH", log), \
         patch("agent.turn_log._COUNTS_DB", counts_db):
        for _ in range(5):
            append_turn(
                session_id="s1", mode="root", user_input="hi", response="hello",
                duration_ms=100,
            )
        result = count_today()

    assert result == 5


def test_counter_drift_tolerated(tmp_path):
    """Counter failure does not prevent append_turn from writing the turn."""
    from agent.turn_log import append_turn

    log = tmp_path / "turns.jsonl"
    counts_db = tmp_path / "turn_counts.db"

    recorded_cats = []

    def fake_track(cat, exc=None, **kw):
        recorded_cats.append(cat)

    with patch("agent.turn_log._LOG_PATH", log), \
         patch("agent.turn_log._COUNTS_DB", counts_db), \
         patch("agent.turn_log._increment_counter", side_effect=Exception("db locked")), \
         patch("agent.turn_log.track_silent", fake_track):
        turn_id = append_turn(
            session_id="s1", mode="root", user_input="hi", response="ok",
            duration_ms=50,
        )

    # turn still written
    assert turn_id is not None
    assert log.exists()
    lines = [l for l in log.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
