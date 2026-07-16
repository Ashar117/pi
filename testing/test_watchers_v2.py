"""Tests for T-206: watchers v2 — analyzed events via conversation_switch."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_wm(tmp_path: Path, agent=None):
    from agent.watchers import WatcherManager
    db = tmp_path / "watchers.db"
    tg = MagicMock()
    return WatcherManager(db_path=db, telegram_send_fn=tg, agent=agent), tg


def _make_agent():
    ag = MagicMock()
    ag.conversation_id = "desktop-1"
    ag.mode = "root"
    ag.messages = []
    ag.process_input = MagicMock(return_value="This file changed because of X.")
    mem = MagicMock()
    mem.load_conversation_turns.return_value = []
    mem.create_conversation.return_value = None
    ag.memory = mem
    return ag


# ── Schema ────────────────────────────────────────────────────────────────────

def test_analyze_column_in_watchers_table(tmp_path):
    import sqlite3
    wm, _ = _make_wm(tmp_path)
    conn = sqlite3.connect(str(tmp_path / "watchers.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(watchers)").fetchall()}
    conn.close()
    assert "analyze" in cols


def test_watcher_add_stores_analyze_flag(tmp_path):
    import sqlite3
    wm, _ = _make_wm(tmp_path)
    wm.watcher_add("w1", "schedule", {"interval_minutes": 30}, analyze=True)
    conn = sqlite3.connect(str(tmp_path / "watchers.db"))
    row = conn.execute("SELECT analyze FROM watchers WHERE name='w1'").fetchone()
    conn.close()
    assert row[0] == 1


# ── T-289: watcher state survives daemon restarts ─────────────────────────────

def test_file_watcher_state_survives_reconstruction(tmp_path):
    """A second WatcherManager on the same db must not re-fire on unchanged state."""
    target = tmp_path / "watched.txt"
    target.write_text("v1", encoding="utf-8")

    wm1, _ = _make_wm(tmp_path)
    wm1.watcher_add("f1", "file", {"path": str(target), "event": "modified"})
    wm1._sweep()  # first sweep just records the baseline (existed=None -> False)

    # Simulate a restart: brand-new manager, same db, empty in-memory state.
    wm2, tg2 = _make_wm(tmp_path)
    wm2._sweep()  # must load state from the db, not re-baseline from scratch
    assert tg2.call_count == 0, "restart must not fire on an unchanged file"


def test_email_watcher_seen_ids_survive_reconstruction(tmp_path, monkeypatch):
    """seen_ids persisted across a restart — same messages must not re-fire."""
    from tools.tools_gmail import GmailTools
    fake_msgs = [{"id": "m1", "from_short": "a", "subject": "hi"}]
    monkeypatch.setattr(GmailTools, "is_configured", lambda self: True)
    monkeypatch.setattr(GmailTools, "gmail_search",
                        lambda self, query, max_results=10: {"success": True, "messages": fake_msgs})

    wm1, tg1 = _make_wm(tmp_path)
    wm1.watcher_add("inbox", "email", {})
    wm1._sweep()
    assert tg1.call_count == 1  # first sighting fires

    wm2, tg2 = _make_wm(tmp_path)  # simulated restart
    wm2._sweep()
    assert tg2.call_count == 0, "restart must not re-alert for the same message ids"


def test_watcher_add_analyze_defaults_false(tmp_path):
    import sqlite3
    wm, _ = _make_wm(tmp_path)
    wm.watcher_add("w2", "schedule", {"interval_minutes": 60})
    conn = sqlite3.connect(str(tmp_path / "watchers.db"))
    row = conn.execute("SELECT analyze FROM watchers WHERE name='w2'").fetchone()
    conn.close()
    assert row[0] == 0


# ── T-277: tool-surface gaps ─────────────────────────────────────────────────

def test_tool_handler_passes_analyze_through(tmp_path):
    """T-277: the watcher tool's add path silently dropped analyze."""
    import sqlite3
    from agent.watchers import _handle_watcher
    wm, _ = _make_wm(tmp_path)
    agent = MagicMock()
    agent.watchers = wm
    r = _handle_watcher(agent, {"action": "add", "name": "an", "type": "schedule",
                                "config": {"interval_minutes": 5}, "analyze": True})
    assert r["success"]
    conn = sqlite3.connect(str(tmp_path / "watchers.db"))
    row = conn.execute("SELECT analyze FROM watchers WHERE name='an'").fetchone()
    conn.close()
    assert row[0] == 1


def test_success_predicate_accepts_list_and_status_shapes():
    """T-277: list/status responses have no success key — must not log as failures."""
    from agent.watchers import TOOLS
    pred = TOOLS[0].success_predicate
    assert pred({"watchers": []})
    assert pred({"total_watchers": 0, "active_watchers": 0, "total_fires": 0,
                 "thread_alive": False, "recent_events": []})
    assert pred({"success": True, "id": "x", "name": "n"})
    assert not pred({"success": False, "error": "boom"})
    assert not pred({"error": "WatcherManager unavailable"})


def test_price_watcher_fires_once_per_crossing(monkeypatch):
    """T-277: price watcher must alert on the crossing, not every sweep."""
    import sys as _sys
    from agent.watchers import _check_price

    class _FakeTicker:
        fast_info = {"lastPrice": 250.0}

    fake_yf = type("yf", (), {"Ticker": staticmethod(lambda t: _FakeTicker())})
    monkeypatch.setitem(_sys.modules, "yfinance", fake_yf)

    cfg = {"ticker": "NVDA", "above": 200}
    fired1, detail1, s1 = _check_price(cfg, {})
    assert fired1 and "250.00" in detail1
    fired2, _, s2 = _check_price(cfg, s1)          # still above — no re-alert
    assert not fired2
    _FakeTicker.fast_info = {"lastPrice": 150.0}   # drops back under
    fired3, _, s3 = _check_price(cfg, s2)
    assert not fired3
    _FakeTicker.fast_info = {"lastPrice": 260.0}   # crosses again
    fired4, _, _ = _check_price(cfg, s3)
    assert fired4


# ── _fire raw mode (unchanged) ────────────────────────────────────────────────

def test_raw_fire_sends_plain_telegram_message(tmp_path):
    wm, tg = _make_wm(tmp_path)
    wm._fire("w1", "mywatch", "file changed", "detail", analyze=False)
    tg.assert_called_once()
    msg = tg.call_args[0][0]
    assert "mywatch" in msg


def test_raw_fire_with_no_telegram_doesnt_crash(tmp_path):
    from agent.watchers import WatcherManager
    db = tmp_path / "watchers.db"
    wm = WatcherManager(db_path=db, telegram_send_fn=None)
    wm._fire("w1", "mywatch", "alert", "detail")  # should not raise


# ── _fire analyzed mode ───────────────────────────────────────────────────────

def test_analyzed_fire_calls_process_input(tmp_path):
    ag = _make_agent()
    wm, tg = _make_wm(tmp_path, agent=ag)
    wm._fire("w1", "mywatch", "file changed", "detail", analyze=True)
    ag.process_input.assert_called_once()
    prompt = ag.process_input.call_args[0][0]
    assert "mywatch" in prompt


def test_analyzed_fire_uses_watchers_conversation(tmp_path):
    ag = _make_agent()
    wm, _ = _make_wm(tmp_path, agent=ag)

    conv_ids_seen = []
    def _capture(text):
        conv_ids_seen.append(ag.conversation_id)
        return "analysis result"
    ag.process_input.side_effect = _capture

    wm._fire("w1", "mywatch", "alert", "detail", analyze=True)
    assert conv_ids_seen[0] == "watchers"


def test_analyzed_fire_restores_desktop_context(tmp_path):
    ag = _make_agent()
    wm, _ = _make_wm(tmp_path, agent=ag)

    original_conv = ag.conversation_id
    wm._fire("w1", "mywatch", "alert", "detail", analyze=True)
    assert ag.conversation_id == original_conv


def test_analyzed_fire_sends_analysis_to_telegram(tmp_path):
    ag = _make_agent()
    ag.process_input.return_value = "The test suite broke because of a bad merge."
    wm, tg = _make_wm(tmp_path, agent=ag)

    wm._fire("w1", "mywatch", "alert", "detail", analyze=True)
    msg = tg.call_args[0][0]
    assert "The test suite broke" in msg


def test_analyzed_fire_falls_back_to_raw_on_agent_error(tmp_path):
    ag = _make_agent()
    ag.process_input.side_effect = RuntimeError("agent down")
    wm, tg = _make_wm(tmp_path, agent=ag)

    wm._fire("w1", "mywatch", "alert msg", "detail", analyze=True)
    msg = tg.call_args[0][0]
    assert "mywatch" in msg  # raw fallback still fires


# ── Rate limit ────────────────────────────────────────────────────────────────

def test_rate_limit_allows_up_to_limit(tmp_path):
    from agent.watchers import _ANALYZE_RATE_LIMIT
    ag = _make_agent()
    wm, tg = _make_wm(tmp_path, agent=ag)

    for _ in range(_ANALYZE_RATE_LIMIT):
        assert wm._within_rate_limit() is True


def test_rate_limit_rejects_beyond_limit(tmp_path):
    from agent.watchers import _ANALYZE_RATE_LIMIT
    ag = _make_agent()
    wm, tg = _make_wm(tmp_path, agent=ag)

    for _ in range(_ANALYZE_RATE_LIMIT):
        wm._within_rate_limit()
    assert wm._within_rate_limit() is False


def test_rate_limit_skips_analysis_when_exceeded(tmp_path):
    from agent.watchers import _ANALYZE_RATE_LIMIT
    ag = _make_agent()
    wm, tg = _make_wm(tmp_path, agent=ag)

    # Exhaust the rate limit
    for _ in range(_ANALYZE_RATE_LIMIT):
        wm._within_rate_limit()

    wm._fire("w1", "mywatch", "alert", "detail", analyze=True)
    # process_input should NOT have been called (rate limited)
    ag.process_input.assert_not_called()


# ── No agent = analyzed falls back to raw ────────────────────────────────────

def test_analyzed_fire_without_agent_sends_raw(tmp_path):
    wm, tg = _make_wm(tmp_path, agent=None)
    wm._fire("w1", "mywatch", "alert msg", "detail", analyze=True)
    tg.assert_called_once()
    msg = tg.call_args[0][0]
    assert "mywatch" in msg


# ── T-274: pi_agent.py <-> TelegramTools wiring contract ─────────────────────
# WatcherManager's telegram_send_fn was wired from a nonexistent
# TelegramTools.send_message attribute for the lifetime of the watchers
# feature — getattr always returned None, so alerts never reached Telegram.
# This guards the exact attribute names pi_agent.py's getattr calls target.

def test_telegram_tools_has_the_methods_pi_agent_wires():
    from tools.tools_telegram import TelegramTools
    assert callable(getattr(TelegramTools, "send", None)), "pi_agent.py wires telegram_send_fn from .send"
    assert callable(getattr(TelegramTools, "send_buttons", None)), "pi_agent.py wires telegram_buttons_fn from .send_buttons"
    assert not hasattr(TelegramTools, "send_message"), (
        "send_message was never a TelegramTools attribute — the old pi_agent.py wiring "
        "silently resolved to None; this must stay absent so the bug can't quietly return"
    )


# ── T-258: email watcher alerts use inline triage buttons ────────────────────

def test_email_watcher_fire_uses_buttons_not_plain_send(tmp_path):
    from agent.watchers import WatcherManager
    db = tmp_path / "watchers.db"
    tg = MagicMock()
    tg_buttons = MagicMock()
    wm = WatcherManager(db_path=db, telegram_send_fn=tg, telegram_buttons_fn=tg_buttons)

    wm._fire("w1", "inbox-watch", "New mail", "New mail from Boss: hi",
             wtype="email", email_message_id="msg-abc123")

    tg_buttons.assert_called_once()
    text, button_specs = tg_buttons.call_args[0]
    labels = [label for label, _ in button_specs]
    callbacks = [cb for _, cb in button_specs]
    assert labels == ["Draft reply", "Add to calendar", "Ignore"]
    assert callbacks == [
        "emailtriage:reply:msg-abc123",
        "emailtriage:cal:msg-abc123",
        "emailtriage:ignore:msg-abc123",
    ]
    tg.assert_not_called()


def test_non_email_watcher_fire_still_uses_plain_send(tmp_path):
    wm, tg = _make_wm(tmp_path)
    wm._telegram_buttons = MagicMock()
    wm._fire("w1", "file-watch", "File changed", "detail", wtype="file")
    tg.assert_called_once()
    wm._telegram_buttons.assert_not_called()


def test_email_watcher_fire_falls_back_when_no_buttons_fn(tmp_path):
    wm, tg = _make_wm(tmp_path)  # telegram_buttons_fn defaults to None
    wm._fire("w1", "inbox-watch", "New mail", "detail",
             wtype="email", email_message_id="msg-1")
    tg.assert_called_once()
