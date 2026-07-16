"""testing/test_pi_agent_hardening.py — T-195/T-196/T-197/T-198 hardening.

Unit tests for the four pi_agent.py hardening fixes. All hermetic — no live API calls.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock


# ── shared fixture helper ──────────────────────────────────────────────────────

def _make_agent():
    """Create a PiAgent with all external deps stubbed (no network)."""
    def fake_input(prompt=""):
        return "n"

    with patch("builtins.input", side_effect=fake_input):
        from pi_agent import PiAgent
        agent = PiAgent()
        agent.mode = "root"

    from datetime import datetime, timezone
    agent._awareness_snapshot_cache = ""
    agent._awareness_last_refresh = datetime.now(timezone.utc)
    patch.object(agent.memory, "memory_search_semantic", return_value=[]).start()
    return agent


# ── T-195: exception redaction ────────────────────────────────────────────────

def test_t195_inner_exception_is_redacted():
    """_process_input_inner's outer except must not leak raw exception text."""
    agent = _make_agent()

    SECRET_KEY = "sk-secret-api-key-1234"

    def boom(*args, **kwargs):
        raise RuntimeError(f"HTTPSConnectionPool(host='api.openai.com'): key={SECRET_KEY}")

    with patch.object(agent, "_respond_via_config", side_effect=boom):
        response = agent._process_input_inner("hello")

    assert SECRET_KEY not in response, (
        f"Raw exception text leaked to user: {response!r}"
    )
    assert "[Pi] Error:" in response or "error" in response.lower()


def test_t195_safe_error_used_in_inner_except():
    """Confirm _safe_error is called (not str(e)) at the inner except site."""
    from agent.redaction import safe_error as real_safe_error
    calls = []

    def tracking_safe_error(e, audience="user"):
        calls.append(audience)
        return real_safe_error(e, audience=audience)

    agent = _make_agent()

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    with patch.object(agent, "_respond_via_config", side_effect=boom), \
         patch("pi_agent._safe_error", side_effect=tracking_safe_error):
        agent._process_input_inner("hello")

    assert "user" in calls, "_safe_error(audience='user') never called in inner except"


# ── T-196: research mode two-step (no input() call) ──────────────────────────

def test_t196_research_mode_sets_pending_flag():
    """'research mode' command must set _pending_research=True, not call input()."""
    agent = _make_agent()

    # If input() is called, this will raise (no side_effect left)
    with patch("builtins.input", side_effect=AssertionError("input() called mid-turn")):
        response = agent._process_input_inner("research mode")

    assert agent._pending_research is True
    assert "question" in response.lower() or "ready" in response.lower()


def test_t196_pending_flag_consumed_on_next_turn():
    """When _pending_research is True, the next process_input becomes the question."""
    agent = _make_agent()
    agent._pending_research = True

    # Stub out research mode so we don't hit live APIs
    with patch("core.research_mode.run_research_mode", return_value="test synthesis"):
        response = agent._process_input_inner("What is quantum entanglement?")

    assert agent._pending_research is False, "_pending_research must be reset after consumption"
    assert "research" in response.lower() or "complete" in response.lower()


def test_t196_no_input_calls_in_turn_path():
    """Verify there are no bare input() calls in the _process_input_inner code path."""
    import ast, inspect, textwrap
    from pi_agent import PiAgent

    src = textwrap.dedent(inspect.getsource(PiAgent._process_input_inner))
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "input":
                assert False, (
                    f"input() call found in _process_input_inner at line {node.lineno}. "
                    "This blocks non-REPL channels (Telegram, voice, daemon)."
                )


# ── T-197: tool result serialization ─────────────────────────────────────────

def test_t197_serialize_handles_non_json_types():
    """datetime/Path/bytes results must not raise TypeError."""
    from pi_agent import PiAgent
    from datetime import datetime, timezone
    from pathlib import Path

    assert isinstance(PiAgent._serialize_tool_result(datetime.now(timezone.utc)), str)
    assert isinstance(PiAgent._serialize_tool_result(Path("/tmp/test")), str)
    assert isinstance(PiAgent._serialize_tool_result(b"raw bytes"), str)
    assert isinstance(PiAgent._serialize_tool_result({"key": datetime.now()}), str)


def test_t197_serialize_truncates_oversized():
    """Results over 32k chars must be truncated with an explicit notice."""
    from pi_agent import PiAgent

    big = "x" * 40_000
    result = PiAgent._serialize_tool_result(big, cap=32_000)
    assert len(result) < 40_000
    assert "truncated" in result


def test_t197_serialize_normal_result_unchanged():
    """Normal-sized string results pass through unchanged."""
    from pi_agent import PiAgent

    normal = json.dumps({"status": "ok", "data": [1, 2, 3]})
    assert PiAgent._serialize_tool_result(normal) == normal


# ── T-198: turns.jsonl real metadata ─────────────────────────────────────────

def test_t198_last_turn_meta_initialized():
    """_last_turn_meta must exist on a fresh agent and have the right shape."""
    agent = _make_agent()
    meta = agent._last_turn_meta
    assert "tools_used" in meta
    assert "cost" in meta
    assert "model" in meta
    assert meta["tools_used"] == []
    assert meta["cost"] == 0.0
    assert meta["model"] == ""


def test_t198_meta_reset_after_process_input():
    """After process_input, _last_turn_meta must be reset to defaults."""
    agent = _make_agent()
    agent._last_turn_meta = {"tools_used": ["web_search"], "cost": 0.01, "model": "groq/x"}

    # Stub turn_log so we can check the values that were passed
    captured = {}

    def fake_append_turn(**kwargs):
        captured.update(kwargs)

    with patch("agent.turn_log.append_turn", side_effect=fake_append_turn):
        # Stub process_input inner path to avoid live API
        with patch.object(agent, "_process_input_inner", return_value="hi"):
            agent.process_input("hello")

    # After the call, meta must be reset
    assert agent._last_turn_meta == {"tools_used": [], "cost": 0.0, "model": ""}
    # And append_turn must have received the real values (not the empty defaults)
    assert captured.get("tools_used") == ["web_search"]
    assert captured.get("cost") == 0.01
    assert captured.get("model") == "groq/x"
