"""
testing/test_agent_golden.py

PHASE 4 GOLDEN TESTS — behaviour-preservation harness.

Captures the current PiAgent behaviour BEFORE the modular refactor begins.
Every test must pass on the current monolithic pi_agent.py and continue to
pass identically after each module move during Phase 4.

Cost:
  - Most tests are FREE: no Claude calls; Supabase round-trips and a single
    Groq call for the session-summary test (~$0).
  - Tests that would call Claude are intentionally avoided — behaviour
    preservation does not require exercising the paid path. The round-trip
    and prompt-engineering tests live in test_memory_roundtrip.py.

Set SKIP_COSTLY=1 in the environment to skip the Groq-calling test.
"""
import builtins
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Suppress monthly-review prompt before importing PiAgent
_real_input = builtins.input
builtins.input = lambda *args, **kwargs: "no"

from pi_agent import PiAgent  # noqa: E402


def _fresh_agent():
    """Return a freshly-built PiAgent. Init does Supabase + key checks; no Claude calls."""
    return PiAgent()


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------

def test_init_subsystems():
    """PiAgent.__init__ wires memory, execution, evolution, claude, groq clients."""
    a = _fresh_agent()
    assert a.memory is not None and hasattr(a.memory, "memory_read"), "memory missing"
    assert a.execution is not None and hasattr(a.execution, "execute_python"), "execution missing"
    assert a.evolution is not None and hasattr(a.evolution, "log_interaction"), "evolution missing"
    assert a.claude is not None, "anthropic client missing"
    assert a.groq is not None, "groq client missing"
    assert isinstance(a.consciousness, str) and len(a.consciousness) > 100, "consciousness not loaded"
    assert isinstance(a.session_id, str) and len(a.session_id) == 8, f"session_id shape: {a.session_id!r}"
    assert a.mode == "normie", f"default mode should be 'normie', got {a.mode!r}"
    assert a.messages == [], "messages should start empty"
    assert a.history == [], "history should start empty"


def test_normie_mode_command():
    a = _fresh_agent()
    a.mode = "root"  # start in root, switch to normie
    out = a.process_input("normie mode")
    assert a.mode == "normie", f"expected mode=normie, got {a.mode}"
    assert "normie mode active" in out.lower(), f"unexpected reply: {out!r}"


def test_root_mode_command():
    a = _fresh_agent()
    out = a.process_input("root mode")
    assert a.mode == "root", f"expected mode=root, got {a.mode}"
    assert "root mode active" in out.lower(), f"unexpected reply: {out!r}"


def test_loose_match_switch_to_root():
    a = _fresh_agent()
    out = a.process_input("switch to root mode")
    assert a.mode == "root", f"loose matcher missed; mode={a.mode}"
    assert "root mode active" in out.lower()


def test_loose_match_natural_phrasing():
    """T-015 / S-010 — punctuation-tolerant short messages."""
    a = _fresh_agent()
    out = a.process_input("can u switch to root mode ?")
    assert a.mode == "root", f"failed loose match; mode={a.mode}, out={out!r}"


def test_loose_match_go_normie():
    a = _fresh_agent()
    a.mode = "root"
    out = a.process_input("go normie")
    assert a.mode == "normie", f"failed loose match 'go normie'; mode={a.mode}"


def test_exit_command():
    a = _fresh_agent()
    out = a.process_input("exit")
    assert out == "EXIT", f"expected 'EXIT', got {out!r}"


def test_analyze_performance_command():
    a = _fresh_agent()
    out = a.process_input("analyze performance")
    assert isinstance(out, str), f"want str, got {type(out)}"
    assert len(out) > 50, f"report too short: {out!r}"
    # Should mention key sections — names came from Phase 2 fix
    lower = out.lower()
    assert any(s in lower for s in ("performance report", "interactions", "tool usage")), (
        f"report missing canonical sections: {out!r}"
    )


def test_get_system_prompt_includes_mode_block_root():
    a = _fresh_agent()
    a.mode = "root"
    p = a._get_system_prompt()
    assert "MODE: ROOT" in p, "root mode block missing from system prompt"
    assert "TOOLS: All 7 ENABLED" in p or "TOOLS: All" in p, "root tool note missing"


def test_get_system_prompt_includes_mode_block_normie():
    a = _fresh_agent()
    a.mode = "normie"
    p = a._get_system_prompt()
    assert "MODE: NORMIE" in p, "normie mode block missing from system prompt"
    assert "TOOLS: NONE" in p, "normie 'no tools' note missing"


def test_get_system_prompt_includes_consciousness_text():
    """Whatever the prompt builder is, it must include the loaded consciousness text."""
    a = _fresh_agent()
    p = a._get_system_prompt()
    # consciousness.txt opens with '# PI CONSCIOUSNESS v1.0'
    assert "PI CONSCIOUSNESS" in p, "consciousness text not in system prompt"


def test_truncation_preserves_pair_at_max_3():
    """T-012 / S-009 — _truncate_messages_safely(max=3) never orphans tool_result."""
    a = _fresh_agent()
    a.messages = []
    # 5 plain pairs (10 msgs)
    for i in range(5):
        a.messages.append({"role": "user", "content": f"u{i}"})
        a.messages.append({"role": "assistant", "content": f"a{i}"})
    # tool round (4 msgs at indices 10-13)
    a.messages.append({"role": "user", "content": "use a tool"})
    a.messages.append({"role": "assistant",
                       "content": [{"type": "tool_use", "id": "t1", "name": "memory_read", "input": {"query": "x"}}]})
    a.messages.append({"role": "user",
                       "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]})
    a.messages.append({"role": "assistant", "content": "done"})
    # 5 more plain pairs (10 msgs at indices 14-23)
    for i in range(5):
        a.messages.append({"role": "user", "content": f"u{10+i}"})
        a.messages.append({"role": "assistant", "content": f"a{10+i}"})
    assert len(a.messages) == 24

    a._truncate_messages_safely(max_messages=3)
    assert a.messages, "truncation removed everything"

    # The slice point must land on a plain user-text message
    first = a.messages[0]
    assert first["role"] == "user", f"first msg role after truncation: {first['role']}"
    assert isinstance(first["content"], str), (
        f"first msg content should be str (plain user text), got: {type(first['content']).__name__}"
    )

    # Belt-and-suspenders: walk the truncated list, check no tool_result is preceded
    # by anything other than a matching tool_use
    for i, msg in enumerate(a.messages):
        if isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    # must have a preceding assistant message with matching tool_use
                    assert i > 0, f"tool_result at idx 0 has no predecessor: {a.messages}"
                    prev = a.messages[i - 1]
                    assert prev["role"] == "assistant"
                    prev_content = prev.get("content", [])
                    if isinstance(prev_content, list):
                        ids = {b.get("id") for b in prev_content if isinstance(b, dict) and b.get("type") == "tool_use"}
                        assert block.get("tool_use_id") in ids, (
                            f"orphan tool_result {block.get('tool_use_id')}; available tool_use ids: {ids}"
                        )


def test_truncation_no_op_when_under_limit():
    a = _fresh_agent()
    a.messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    before = list(a.messages)
    a._truncate_messages_safely(max_messages=20)
    assert a.messages == before, "truncation modified messages despite being under limit"


def test_extract_text_from_messages():
    """_extract_text_from_messages produces readable text from mixed content shapes."""
    a = _fresh_agent()
    a.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "assistant",
         "content": [{"type": "tool_use", "id": "t1", "name": "memory_read", "input": {"query": "x"}}]},
        {"role": "user",
         "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]
    out = a._extract_text_from_messages(n=10)
    assert isinstance(out, str)
    assert "user: hello" in out
    assert "assistant: hi" in out
    assert "tool_result" in out  # block formatter mentions it


def test_generate_session_summary_nonempty():
    """COSTLY (Groq, ~$0). Skipped when SKIP_COSTLY env is set."""
    if os.environ.get("SKIP_COSTLY"):
        print("    SKIPPED (SKIP_COSTLY set)")
        return
    a = _fresh_agent()
    a.history = [
        {"role": "user", "content": "what is 2 plus 2?"},
        {"role": "assistant", "content": "4"},
        {"role": "user", "content": "and 3 times 5?"},
        {"role": "assistant", "content": "15"},
    ]
    summary = a._generate_session_summary()
    assert isinstance(summary, str), f"summary type: {type(summary)}"
    assert len(summary) > 5, f"summary too short: {summary!r}"


# ----------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------

def main():
    tests = [
        ("init wires all subsystems", test_init_subsystems),
        ("'normie mode' command", test_normie_mode_command),
        ("'root mode' command", test_root_mode_command),
        ("loose match: 'switch to root mode'", test_loose_match_switch_to_root),
        ("loose match: 'can u switch to root mode ?'", test_loose_match_natural_phrasing),
        ("loose match: 'go normie'", test_loose_match_go_normie),
        ("'exit' returns 'EXIT'", test_exit_command),
        ("'analyze performance' returns report string", test_analyze_performance_command),
        ("_get_system_prompt includes ROOT mode block", test_get_system_prompt_includes_mode_block_root),
        ("_get_system_prompt includes NORMIE mode block", test_get_system_prompt_includes_mode_block_normie),
        ("_get_system_prompt includes consciousness", test_get_system_prompt_includes_consciousness_text),
        ("_truncate_messages_safely(max=3) preserves tool pairs", test_truncation_preserves_pair_at_max_3),
        ("_truncate_messages_safely no-op under limit", test_truncation_no_op_when_under_limit),
        ("_extract_text_from_messages handles mixed shapes", test_extract_text_from_messages),
        ("_generate_session_summary returns non-empty (Groq)", test_generate_session_summary_nonempty),
    ]
    print("\n=== test_agent_golden.py ===\n")
    failed = []
    for name, fn in tests:
        print(f"[*] {name} ...")
        try:
            fn()
            print(f"    PASSED\n")
        except AssertionError as e:
            print(f"    FAILED: {str(e)[:300]}\n")
            failed.append(name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            failed.append(name)
    print("=" * 60)
    if failed:
        print(f"{len(failed)}/{len(tests)} failed: {failed}")
        sys.exit(1)
    print(f"All {len(tests)} tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    finally:
        builtins.input = _real_input
