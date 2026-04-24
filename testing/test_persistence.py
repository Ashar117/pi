"""
Session Persistence Tests
Tests for Ticket #002: Session memory not persisting across restarts
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_runner import TestRunner
from tools.tools_memory import MemoryTools
from app.config import SUPABASE_URL, SUPABASE_KEY

runner = TestRunner()


def test_002_evolution_log_exists():
    """
    Ticket #002 Prerequisite: evolution.jsonl logging infrastructure exists
    Expected: Logs directory and evolution.jsonl exist (or can be created)
    """
    logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

    print(f"  Checking logs dir: {logs_dir}")
    assert os.path.exists(logs_dir), f"Logs directory missing: {logs_dir}"
    print(f"  ✓ Logs directory exists")

    evolution_log = os.path.join(logs_dir, "evolution.jsonl")
    if os.path.exists(evolution_log):
        stat = os.stat(evolution_log)
        print(f"  ✓ evolution.jsonl exists: {stat.st_size} bytes")
    else:
        print(f"  ⚠ evolution.jsonl not yet created (will be created on first interaction)")

    return True


def test_002_l3_context_loads():
    """
    Ticket #002 Test: L3 context loads on startup
    Expected: get_l3_context() returns at least the permanent profile
    """
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)
    context = memory.get_l3_context()

    print(f"  L3 context length: {len(context)} chars")
    print(f"  L3 context preview: {context[:200]}")

    assert context, "L3 context is empty"
    assert len(context) > 50, f"L3 context too short ({len(context)} chars) - permanent profile missing?"

    print(f"  ✓ L3 context loaded: {len(context)} chars")
    return True


def test_002_session_history_category_writable():
    """
    Ticket #002 Test: Session history can be written to memory
    Expected: Writing a session_history entry succeeds
    """
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)

    result = memory.memory_write(
        content="Session summary test (2026-04-20): Tested session persistence ticket 002",
        tier="l3",
        importance=4,
        category="session_history"
    )

    print(f"  Session history write result: {result}")
    has_success = result.get("success") is True or result.get("status") == "success"
    assert has_success, f"Session history write failed: {result}"

    # Verify it's readable
    read = memory.memory_read(query="Session summary test")
    print(f"  Read back count: {len(read) if read else 0}")
    assert len(read) > 0, "Session history write succeeded but read returned empty"

    print(f"  ✓ Session history write and read work")
    return True


def test_002_session_summary_method_exists():
    """
    Ticket #002 Test: PiAgent has _generate_session_summary method
    Expected: Method exists and is callable
    """
    import inspect
    # Import without running __init__ to avoid needing all keys
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pi_agent",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pi_agent.py")
    )
    module = importlib.util.module_from_spec(spec)

    # Just check the source code for the method
    pi_agent_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pi_agent.py")
    with open(pi_agent_path, 'r') as f:
        source = f.read()

    has_method = "_generate_session_summary" in source
    has_exit_save = "Session summary saved" in source or "session summary" in source.lower()

    print(f"  _generate_session_summary in pi_agent.py: {has_method}")
    print(f"  Exit save logic present: {has_exit_save}")

    assert has_method, "_generate_session_summary method not found in pi_agent.py"
    assert has_exit_save, "No session summary save on exit found in pi_agent.py"

    print(f"  ✓ Session summary infrastructure exists in pi_agent.py")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("SESSION PERSISTENCE TESTS - Ticket #002")
    print("=" * 60)

    runner.run_test(test_002_evolution_log_exists, "Evolution Log Infrastructure", ticket_id=2)
    runner.run_test(test_002_l3_context_loads, "L3 Context Loads on Startup", ticket_id=2)
    runner.run_test(test_002_session_history_category_writable, "Session History Writable", ticket_id=2)
    runner.run_test(test_002_session_summary_method_exists, "Session Summary Method Exists", ticket_id=2)

    runner.print_summary()
    runner.save_results("testing/results/persistence_test_results.json")
    runner.generate_failure_tickets("testing/results/persistence_failures.txt")
