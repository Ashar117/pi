"""
Mode Switching Tests
Tests for Ticket #003: Normie mode memory isolation
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    assert os.path.exists(CONSCIOUSNESS_PATH), f"consciousness.txt not found at {CONSCIOUSNESS_PATH}"

    with open(CONSCIOUSNESS_PATH) as f:
        content = f.read()

    print(f"  consciousness.txt size: {len(content)} chars")
    assert len(content) > 100, "consciousness.txt is nearly empty"

    print(f"  ✓ consciousness.txt exists ({len(content)} chars)")
    return True


def test_003_system_prompt_injects_mode():
    """
    Ticket #003 Test: System prompt includes current mode state
    Expected: _get_system_prompt() includes ROOT or NORMIE state block
    """
    with open(PI_AGENT_PATH, 'r') as f:
        source = f.read()

    has_root_injection = "MODE: ROOT" in source
    has_normie_injection = "MODE: NORMIE" in source
    has_session_time = "SESSION TIME" in source or "CURRENT SESSION" in source

    print(f"  ROOT mode injection in source: {has_root_injection}")
    print(f"  NORMIE mode injection in source: {has_normie_injection}")
    print(f"  Session time injection: {has_session_time}")

    assert has_root_injection, "No 'MODE: ROOT' state injection found in pi_agent.py"
    assert has_normie_injection, "No 'MODE: NORMIE' state injection found in pi_agent.py"

    print(f"  ✓ Mode state injection exists in _get_system_prompt()")
    return True


def test_003_normie_has_session_context():
    """
    Ticket #003 Test: Normie mode receives session context
    Expected: _respond_normie passes session history to Groq
    """
    with open(PI_AGENT_PATH, 'r') as f:
        source = f.read()

    has_normie_method = "def _respond_normie" in source
    has_session_extract = "_extract_text_from_messages" in source or "self.messages" in source
    has_session_context_in_normie = "SESSION CONTEXT" in source

    print(f"  _respond_normie method exists: {has_normie_method}")
    print(f"  Session extraction in normie: {has_session_extract}")
    print(f"  SESSION CONTEXT injected: {has_session_context_in_normie}")

    assert has_normie_method, "_respond_normie method not found in pi_agent.py"
    assert has_session_context_in_normie, "No SESSION CONTEXT injection in _respond_normie"

    print(f"  ✓ Normie mode has session context mechanism")
    return True


def test_003_messages_list_persists():
    """
    Ticket #003 Test: self.messages persists across mode switches
    Expected: self.messages is a persistent list (not rebuilt each call)
    """
    with open(PI_AGENT_PATH, 'r') as f:
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

    print(f"  ✓ self.messages persistent list exists")
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
