"""
Mode Switching Tests
Tests for Ticket #003: Normie mode memory isolation
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from test_runner import TestRunner

runner = TestRunner()

PI_AGENT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pi_agent.py")
CONSCIOUSNESS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts", "consciousness.txt")


def test_003_consciousness_file_exists():
    """
    Ticket #003 Prerequisite: consciousness.txt exists
    Expected: File exists and is non-empty
    """
    print(f"  Checking: {CONSCIOUSNESS_PATH}")
    if not os.path.exists(CONSCIOUSNESS_PATH):
        pytest.skip("prompts/consciousness.txt is private/gitignored — not present in this checkout")

    with open(CONSCIOUSNESS_PATH) as f:
        content = f.read()

    print(f"  consciousness.txt size: {len(content)} chars")
    assert len(content) > 100, "consciousness.txt is nearly empty"

    print(f"  [OK]consciousness.txt exists ({len(content)} chars)")
    return True


def test_003_system_prompt_injects_mode():
    """
    Ticket #003 Test: System prompt includes current mode state
    Expected: _get_system_prompt() includes ROOT or NORMIE state block
    """
    # After Phase 4 refactor, mode-block injection lives in agent/prompt.py
    prompt_path = os.path.join(os.path.dirname(PI_AGENT_PATH), "agent", "prompt.py")
    search_paths = [PI_AGENT_PATH, prompt_path]
    combined_source = ""
    for p in search_paths:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                combined_source += f.read()

    has_root_injection = "MODE: ROOT" in combined_source
    has_normie_injection = "MODE: NORMIE" in combined_source
    has_session_time = "SESSION TIME" in combined_source or "CURRENT SESSION" in combined_source

    print(f"  ROOT mode injection in source: {has_root_injection}")
    print(f"  NORMIE mode injection in source: {has_normie_injection}")
    print(f"  Session time injection: {has_session_time}")

    assert has_root_injection, "No 'MODE: ROOT' state injection found in pi_agent.py or agent/prompt.py"
    assert has_normie_injection, "No 'MODE: NORMIE' state injection found in pi_agent.py or agent/prompt.py"

    print(f"  [OK]Mode state injection exists in _get_system_prompt()")
    return True


def test_003_normie_has_session_context():
    """
    Ticket #003 Test: Normie mode receives session context
    T-089 R8 Stage C: _respond_normie collapsed into _respond_via_config.
    Verify the unified path still carries session context for normie.
    """
    with open(PI_AGENT_PATH, 'r', encoding='utf-8') as f:
        source = f.read()

    # R8 Stage C: single unified method replaces _respond_normie
    has_unified_method = "def _respond_via_config" in source
    has_session_extract = "_extract_text_from_messages" in source or "self.messages" in source
    has_session_context = "SESSION CONTEXT" in source
    has_session_ctx_inject = "session_ctx_inject" in source

    print(f"  _respond_via_config (unified) exists: {has_unified_method}")
    print(f"  Session extraction present: {has_session_extract}")
    print(f"  SESSION CONTEXT injected: {has_session_context}")
    print(f"  session_ctx_inject flag used: {has_session_ctx_inject}")

    assert has_unified_method, "_respond_via_config not found — unified path missing"
    assert has_session_context, "No SESSION CONTEXT injection in unified path"
    assert has_session_ctx_inject, "session_ctx_inject flag not referenced"

    print(f"  [OK]Normie session context handled via _respond_via_config + session_ctx_inject")
    return True


def test_003_messages_list_persists():
    """
    Ticket #003 Test: self.messages persists across mode switches
    Expected: self.messages is a persistent list (not rebuilt each call)
    """
    with open(PI_AGENT_PATH, 'r', encoding='utf-8') as f:
        source = f.read()

    has_self_messages = "self.messages = []" in source
    messages_in_init = False

    # Check it's initialized in __init__
    init_section = source[source.find("def __init__"):source.find("def __init__") + 1000]
    messages_in_init = "self.messages" in init_section

    print(f"  self.messages initialized: {has_self_messages}")
    print(f"  self.messages in __init__: {messages_in_init}")

    assert has_self_messages, "self.messages not found in pi_agent.py"
    assert messages_in_init, "self.messages not initialized in __init__"

    print(f"  [OK]self.messages persistent list exists")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("MODE SWITCHING TESTS - Ticket #003")
    print("=" * 60)

    runner.run_test(test_003_consciousness_file_exists, "Consciousness File Exists", ticket_id=3)
    runner.run_test(test_003_system_prompt_injects_mode, "System Prompt Mode Injection", ticket_id=3)
    runner.run_test(test_003_normie_has_session_context, "Normie Has Session Context", ticket_id=3)
    runner.run_test(test_003_messages_list_persists, "Messages List Persists", ticket_id=3)

    runner.print_summary()
    runner.save_results("testing/results/mode_test_results.json")
    runner.generate_failure_tickets("testing/results/mode_failures.txt")
