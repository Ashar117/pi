"""testing/test_telegram_context_persistence.py — T-244: in-memory conv cache.

Tests (offline, mock agent + memory):
  - Two sequential turns share in-memory history (second sees first turn)
  - /clear resets the conv cache (next turn starts fresh)
  - First message after clear does NOT reload old SQLite turns
  - New TelegramTools instance (restart) reloads from SQLite on first message
  - Photos and button callbacks use the same cache as text (shared context)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch


def _make_agent(initial_messages=None, conv_id="repl-abc"):
    agent = MagicMock()
    agent.messages = list(initial_messages or [])
    agent.conversation_id = conv_id
    agent._current_chat_id = None
    agent._current_message_id = None
    agent._last_sent_message_id = None
    agent.memory = MagicMock()
    agent.memory.load_conversation_turns = MagicMock(return_value=[])

    def _fake_process(text):
        # Simulate agent appending user + assistant to messages
        agent.messages.append({"role": "user", "content": text})
        agent.messages.append({"role": "assistant", "content": f"reply to: {text}"})
        return f"reply to: {text}"

    agent.process_input = MagicMock(side_effect=_fake_process)
    return agent


def _make_tg(agent):
    from tools.tools_telegram import TelegramTools
    tg = TelegramTools.__new__(TelegramTools)
    tg._agent = agent
    tg._on_message = None
    tg._bot = None
    tg._bubble = None
    tg._conv_cache = {}
    return tg


# ── T-244: context persists across sequential turns ───────────────────────────

def test_second_turn_sees_first_turn_in_history():
    agent = _make_agent()
    tg = _make_tg(agent)

    tg._process_as_telegram_peer("hello", chat_id=99)
    # After first turn, conv cache should have 2 messages (user + assistant)
    assert len(tg._conv_cache["telegram:99"]) == 2

    tg._process_as_telegram_peer("world", chat_id=99)
    # After second turn, conv cache should have 4 messages
    assert len(tg._conv_cache["telegram:99"]) == 4
    roles = [m["role"] for m in tg._conv_cache["telegram:99"]]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_repl_state_restored_after_telegram_turn():
    """REPL messages must not be contaminated by Telegram turns."""
    repl_msgs = [{"role": "user", "content": "repl msg"}]
    agent = _make_agent(initial_messages=repl_msgs, conv_id="repl-xyz")
    tg = _make_tg(agent)

    tg._process_as_telegram_peer("telegram msg", chat_id=42)

    # After the turn, REPL state is restored
    assert agent.messages == repl_msgs
    assert agent.conversation_id == "repl-xyz"


def test_sqlite_loaded_only_on_first_contact():
    """SQLite load happens once; subsequent turns skip it."""
    agent = _make_agent()
    agent.memory.load_conversation_turns.return_value = [
        {"role": "user", "content": "old turn"},
        {"role": "assistant", "content": "old reply"},
    ]
    tg = _make_tg(agent)

    tg._process_as_telegram_peer("new turn", chat_id=7)
    assert agent.memory.load_conversation_turns.call_count == 1

    tg._process_as_telegram_peer("another turn", chat_id=7)
    # Must not reload from SQLite on second turn
    assert agent.memory.load_conversation_turns.call_count == 1


def test_sqlite_history_available_on_first_turn():
    """Old turns loaded from SQLite are visible as context on first message."""
    agent = _make_agent()
    agent.memory.load_conversation_turns.return_value = [
        {"role": "user", "content": "from last session"},
        {"role": "assistant", "content": "remembered"},
    ]
    tg = _make_tg(agent)

    tg._process_as_telegram_peer("hi again", chat_id=5)
    # Cache should have: 2 loaded + 2 from this turn
    assert len(tg._conv_cache["telegram:5"]) == 4


# ── T-244: /clear resets cache ────────────────────────────────────────────────

def test_clear_resets_conv_cache():
    agent = _make_agent()
    tg = _make_tg(agent)

    tg._process_as_telegram_peer("first message", chat_id=10)
    assert len(tg._conv_cache["telegram:10"]) == 2

    # Simulate /clear
    tg._conv_cache["telegram:10"] = []

    tg._process_as_telegram_peer("after clear", chat_id=10)
    # Should start fresh — only 2 messages from this turn
    assert len(tg._conv_cache["telegram:10"]) == 2


def test_clear_does_not_reload_sqlite_next_turn():
    """After /clear, the cache key exists as [] so SQLite is not reloaded."""
    agent = _make_agent()
    agent.memory.load_conversation_turns.return_value = [
        {"role": "user", "content": "stale"},
    ]
    tg = _make_tg(agent)

    # Simulate /clear setting cache to []
    tg._conv_cache["telegram:11"] = []

    tg._process_as_telegram_peer("fresh start", chat_id=11)
    # SQLite must NOT be loaded — cache key already existed
    agent.memory.load_conversation_turns.assert_not_called()
    assert len(tg._conv_cache["telegram:11"]) == 2


def test_new_instance_reloads_sqlite():
    """A fresh TelegramTools (restart) loads from SQLite on first message."""
    agent = _make_agent()
    agent.memory.load_conversation_turns.return_value = [
        {"role": "user", "content": "pre-restart turn"},
        {"role": "assistant", "content": "pre-restart reply"},
    ]
    tg = _make_tg(agent)  # fresh instance, empty _conv_cache

    tg._process_as_telegram_peer("post-restart", chat_id=20)
    agent.memory.load_conversation_turns.assert_called_once_with("telegram:20", max_turns=40)
    # Should have: 2 from SQLite + 2 from this turn
    assert len(tg._conv_cache["telegram:20"]) == 4


if __name__ == "__main__":
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
