"""T-152 — End-to-end conversation coherence harness.

The integration-level counterpart to test_context_fidelity.py (which is unit
level). This drives a SCRIPTED multi-turn conversation through the real
PiAgent.process_input path in each mode, with a FAKE router that records
exactly what the model receives. No network, no API cost.

The invariant under test is the emergent property the suite never checked and
that T-148 restored: across a conversation, the model can SEE Pi's own prior
replies (and the user's). This is the harness whose absence let the keystone
context-drop bug ship green. Future context/memory tickets (T-149/T-150/T-151)
extend the scenarios here rather than writing fresh synthetic-string units.

Cost: free. PiAgent.__init__ is offline-safe (lazy Supabase, T-075); the only
LLM entry point, router.chat, is monkeypatched.
"""
import builtins
import json
import os
import sys
import copy

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Suppress any monthly-review input() prompt before importing PiAgent.
_real_input = builtins.input
builtins.input = lambda *a, **k: "no"

from pi_agent import PiAgent          # noqa: E402
from core.llm_router import LLMResponse  # noqa: E402


class RecordingRouter:
    """Stands in for agent.router. Records every chat() call's system + messages
    and returns a scripted, deterministic reply (no tool calls)."""

    def __init__(self, replies):
        # replies: list of assistant texts, consumed in order; last one repeats.
        self._replies = list(replies)
        self._i = 0
        self.calls = []  # each: {"system": <str>, "messages": <deepcopy list>}

    def chat(self, messages, system=None, tools=None, max_tokens=None, tier=None, **kw):
        self.calls.append({
            "system": _flatten_system(system),
            "messages": copy.deepcopy(messages),
        })
        text = self._replies[min(self._i, len(self._replies) - 1)]
        self._i += 1
        return LLMResponse(text=text, provider="fake", model="fake-1",
                           stop_reason="end_turn", tokens_in=10, tokens_out=10)


def _flatten_system(system):
    """system may be a str or the (static, warm, dynamic) cache tuple."""
    if isinstance(system, (tuple, list)):
        return "\n\n".join(str(p) for p in system if p)
    return str(system or "")


def _serialize_messages(messages):
    """Flatten a canonical message list to searchable text (both shapes)."""
    out = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    out.append(b.get("text", "") or str(b.get("content", "")))
    return "\n".join(out)


def _what_model_saw(call):
    """Everything visible to the model on a given turn: system + message array."""
    return call["system"] + "\n" + _serialize_messages(call["messages"])


# PiAgent.__init__ is the slow part (offline Supabase retries), so build one
# agent per mode for the whole module and reset conversation state per test.
_AGENT_CACHE = {}


def _agent_in_mode(mode, replies):
    a = _AGENT_CACHE.get(mode)
    if a is None:
        a = PiAgent()
        a._async_log = lambda *args, **kwargs: None  # no network side effects
        _AGENT_CACHE[mode] = a
    a.mode = mode
    a.messages = []
    a.history = []
    a.turn_number = 0
    a._normie_handoff_context = ""
    a.router = RecordingRouter(replies)
    return a


def _drive(agent, user_turns):
    """Feed each user turn through process_input; return the recording router."""
    for turn in user_turns:
        agent.process_input(turn)
    return agent.router


# ── Core coherence invariant: Pi can see its own prior reply ─────────────────

@pytest.mark.parametrize("mode", ["root", "normie"])
def test_pi_sees_its_own_prior_reply(mode):
    replies = [
        "Noted — your project codename is BLUEHERON.",
        "It is BLUEHERON, as you told me.",
    ]
    agent = _agent_in_mode(mode, replies)
    _drive(agent, [
        "remember my project codename is BLUEHERON",
        "what's the codename?",
    ])
    # On the SECOND turn, the model must have been able to see its own first
    # reply containing BLUEHERON (via message array in root, via session_ctx
    # in normie). Pre-T-148 this was dropped entirely.
    second_call = agent.router.calls[1]
    assert "BLUEHERON" in _what_model_saw(second_call), (
        f"[{mode}] Pi could not see its own prior reply — T-148 regression"
    )


@pytest.mark.parametrize("mode", ["root", "normie"])
def test_user_statement_visible_next_turn(mode):
    agent = _agent_in_mode(mode, ["ok", "ok"])
    _drive(agent, [
        "my flight is on Tuesday at 9am",
        "is that morning or evening?",
    ])
    saw = _what_model_saw(agent.router.calls[1])
    assert "Tuesday" in saw, f"[{mode}] user's prior statement was dropped"


# ── Structural safety: never empty, never orphaned tool pairs ────────────────

@pytest.mark.parametrize("mode", ["root", "normie"])
def test_messages_never_empty(mode):
    agent = _agent_in_mode(mode, ["a", "b", "c", "d", "e", "f"])
    _drive(agent, [f"message number {i}" for i in range(6)])
    for n, call in enumerate(agent.router.calls):
        assert len(call["messages"]) > 0, f"[{mode}] empty messages on turn {n}"


def test_root_history_accumulates():
    """Root sends the full message array — it must grow across turns."""
    agent = _agent_in_mode("root", ["r1", "r2", "r3"])
    _drive(agent, ["turn one", "turn two", "turn three"])
    lens = [len(c["messages"]) for c in agent.router.calls]
    assert lens == sorted(lens) and lens[-1] > lens[0], (
        f"root message array did not accumulate context: {lens}"
    )


def test_no_orphaned_tool_result_in_sent_messages():
    """Whatever is sent must never start with a tool_result (would 400 a real API)."""
    agent = _agent_in_mode("root", ["x", "y", "z"])
    _drive(agent, ["hello", "how are you", "thanks"])
    for call in agent.router.calls:
        first = call["messages"][0]
        if isinstance(first.get("content"), list):
            for b in first["content"]:
                assert not (isinstance(b, dict) and b.get("type") == "tool_result"), (
                    "sent message array begins with an orphaned tool_result"
                )


# ── T-149: normie now sends a real bounded multi-turn message array ──────────

def test_normie_sends_multi_message_array():
    """Pre-T-149 normie sent only the current turn ([1 message]). Now it sends
    a real bounded history so Groq/Cerebras see prior turns natively."""
    agent = _agent_in_mode("normie", ["r1", "r2", "r3"])
    _drive(agent, ["first thing", "second thing", "third thing"])
    last = agent.router.calls[-1]["messages"]
    assert len(last) > 1, (
        f"normie still sending single-message context: {len(last)} message(s)"
    )


def test_normie_fact_survives_within_window():
    """A fact stated several turns back (inside the 16-message window) must
    still be visible to normie — the whole point of T-149."""
    replies = [f"ack {i}" for i in range(8)]
    agent = _agent_in_mode("normie", replies)
    _drive(agent, [
        "my cat's name is Pixel",   # turn 1
        "what's 2+2",               # 2
        "tell me a fact",           # 3
        "what's the weather like",  # 4 (no awareness snapshot -> falls through)
        "what is my cat's name?",   # 5 — must recall Pixel
    ])
    saw = _what_model_saw(agent.router.calls[-1])
    assert "Pixel" in saw, "normie lost a fact that is well within its context window"


# ── T-142: /newchat resets short-term context, keeps conversation_id fresh ───

def test_newchat_resets_short_term_context():
    agent = _agent_in_mode("root", ["r1", "r2"])
    _drive(agent, ["remember the codename is BLUEHERON", "and the deadline is friday"])
    assert len(agent.messages) > 0 and len(agent.history) > 0
    old_cid = agent.conversation_id
    out = agent.process_input("/newchat")
    assert "new chat" in out.lower()
    assert agent.messages == [], "short-term messages not cleared"
    assert agent.history == [], "history not cleared"
    assert agent.conversation_id != old_cid, "conversation_id did not rotate"


def test_newchat_natural_phrasing():
    agent = _agent_in_mode("normie", ["r1"])
    _drive(agent, ["hello there"])
    out = agent.process_input("new chat")
    assert "new chat" in out.lower() and agent.messages == []


def teardown_module(module):
    builtins.input = _real_input
