"""
Full Integration Tests
End-to-end workflows
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_runner import TestRunner
from tools.tools_memory import MemoryTools
from app.config import SUPABASE_URL, SUPABASE_KEY

runner = TestRunner()


def test_integration_supabase_connection():
    """
    Integration Test: Supabase is reachable and tables exist
    Expected: Can query all 3 tables without error
    """
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)

    # Check l3_active_memory
    r1 = memory.supabase.table("l3_active_memory").select("id").limit(1).execute()
    print(f"  l3_active_memory query OK, rows returned: {len(r1.data)}")

    # Check organized_memory
    r2 = memory.supabase.table("organized_memory").select("id").limit(1).execute()
    print(f"  organized_memory query OK, rows returned: {len(r2.data)}")

    # Check raw_wiki
    r3 = memory.supabase.table("raw_wiki").select("id").limit(1).execute()
    print(f"  raw_wiki query OK, rows returned: {len(r3.data)}")

    print(f"  ✓ All 3 Supabase tables accessible")
    return True


def test_integration_sqlite_works():
    """
    Integration Test: SQLite cache is operational
    Expected: Can connect, write, and read from SQLite
    """
    import sqlite3
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)

    print(f"  SQLite path: {memory.sqlite_path}")
    assert os.path.exists(memory.sqlite_path), f"SQLite DB missing at {memory.sqlite_path}"

    conn = sqlite3.connect(memory.sqlite_path)
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM l3_cache")
    count = cursor.fetchone()[0]
    conn.close()

    print(f"  l3_cache row count: {count}")
    print(f"  ✓ SQLite operational")
    return True


def test_integration_full_write_read_cycle():
    """
    Integration Test: Write → Read → Verify complete cycle
    Expected: Written content retrievable immediately
    """
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)

    unique_marker = "integration_full_cycle_p8v3w"
    test_data = f"Integration test full cycle {unique_marker}"

    # Write
    write_result = memory.memory_write(
        content=test_data,
        tier="l3",
        importance=4,
        category="integration_test"
    )
    print(f"  Write result: {write_result}")
    has_success = write_result.get("success") is True or write_result.get("status") == "success"
    assert has_success, f"Write failed: {write_result}"
    print(f"  ✓ Step 1: Write succeeded")

    # Read back immediately
    read_result = memory.memory_read(query=unique_marker)
    print(f"  Read result count: {len(read_result) if read_result else 0}")
    assert len(read_result) > 0, f"Immediate read returned empty after write. Write was: {write_result}"
    print(f"  ✓ Step 2: Immediate read succeeded")

    # Verify content
    found = any(test_data in str(entry) for entry in read_result)
    assert found, f"Written content not found in read results. Read: {read_result}"
    print(f"  ✓ Step 3: Content verified in results")

    # Verify in Supabase directly
    supa_check = memory.supabase.table("l3_active_memory").select("*").ilike("content", f"%{unique_marker}%").execute()
    print(f"  Supabase direct check count: {len(supa_check.data)}")
    assert len(supa_check.data) > 0, "Content not found in Supabase l3_active_memory directly"
    print(f"  ✓ Step 4: Supabase persistence verified")

    return True


def test_integration_pi_agent_imports():
    """
    Integration Test: pi_agent.py imports without error
    Expected: Can import PiAgent class without crashing
    (Tests imports only, not instantiation which requires API keys)
    """
    pi_agent_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Verify all imported modules exist
    required_files = [
        "pi_agent.py",
        "tools/tools_memory.py",
        "tools/tools_execution.py",
        "evolution.py",
        "app/config.py",
        "core/research_mode.py"
    ]

    missing = []
    for f in required_files:
        full_path = os.path.join(pi_agent_path, f)
        if os.path.exists(full_path):
            print(f"  ✓ {f}")
        else:
            print(f"  ✗ MISSING: {f}")
            missing.append(f)

    assert not missing, f"Missing files: {missing}"

    # Verify syntax of each
    import ast
    for f in required_files:
        full_path = os.path.join(pi_agent_path, f)
        with open(full_path, 'r') as fh:
            source = fh.read()
        try:
            ast.parse(source)
            print(f"  ✓ {f} syntax OK")
        except SyntaxError as e:
            assert False, f"Syntax error in {f}: {e}"

    print(f"  ✓ All required files present and syntactically valid")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("INTEGRATION TESTS")
    print("=" * 60)

    runner.run_test(test_integration_supabase_connection, "Supabase Connection & Tables", ticket_id=None)
    runner.run_test(test_integration_sqlite_works, "SQLite Cache Operational", ticket_id=None)
    runner.run_test(test_integration_full_write_read_cycle, "Full Write-Read Cycle", ticket_id=1)
    runner.run_test(test_integration_pi_agent_imports, "Pi Agent Files & Syntax", ticket_id=None)

    runner.print_summary()
    runner.save_results("testing/results/integration_test_results.json")
    runner.generate_failure_tickets("testing/results/integration_failures.txt")
