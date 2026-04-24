"""
Memory System Tests
Tests for tools/tools_memory.py
Ticket #001: Memory Read Failure
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_runner import TestRunner
from tools.tools_memory import MemoryTools
from app.config import SUPABASE_URL, SUPABASE_KEY

runner = TestRunner()


def test_001_memory_write():
    """
    Ticket #001 Test: Memory write functionality
    Expected: Write succeeds and returns success indicator
    """
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)

    result = memory.memory_write(
        content="Test write for ticket 001",
        tier="l3",
        importance=3,
        category="test"
    )

    print(f"  Write result: {result}")
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    # Check either 'success' or 'status' key
    has_success = result.get("success") is True or result.get("status") == "success"
    assert has_success, f"Write failed: {result}"

    return True


def test_001_memory_read_after_write():
    """
    Ticket #001 Test: Memory read after write
    Expected: Read returns the data that was just written
    """
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)

    test_content = "Test read for ticket 001 - unique string xk9q7z"
    write_result = memory.memory_write(
        content=test_content,
        tier="l3",
        importance=3,
        category="test"
    )
    print(f"  Write result: {write_result}")
    has_success = write_result.get("success") is True or write_result.get("status") == "success"
    assert has_success, f"Write failed: {write_result}"

    # Now read it back
    read_result = memory.memory_read(query="xk9q7z")

    print(f"  Read result type: {type(read_result)}")
    print(f"  Read result count: {len(read_result) if read_result else 0}")
    print(f"  Read result: {read_result}")

    assert read_result is not None, "Read returned None"
    assert len(read_result) > 0, f"Read returned empty list"

    found = any(test_content in str(entry) for entry in read_result)
    assert found, f"Test content not found in read results. Results: {read_result}"

    print(f"  ✓ Test content found in results")
    return True


def test_001_memory_bulk_read():
    """
    Ticket #001 Test: Bulk memory read (multiple items)
    Expected: Empty query returns all recent entries
    """
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)

    test_items = [
        "Bulk test item alpha",
        "Bulk test item beta",
        "Bulk test item gamma"
    ]

    for item in test_items:
        result = memory.memory_write(
            content=item,
            tier="l3",
            importance=3,
            category="bulk_test"
        )
        has_success = result.get("success") is True or result.get("status") == "success"
        assert has_success, f"Failed to write: {item}, result: {result}"

    # Read all items with empty query
    read_result = memory.memory_read(query="")

    print(f"  Items written: {len(test_items)}")
    print(f"  Items read (empty query): {len(read_result) if read_result else 0}")

    assert read_result is not None, "Read returned None"
    assert len(read_result) > 0, "Empty query returned no results"

    # Check at least one bulk item is present
    all_content = str(read_result)
    found_any = any(item in all_content for item in test_items)
    assert found_any, f"None of bulk items found in read results"

    print(f"  ✓ Bulk read returned {len(read_result)} entries")
    return True


def test_001_single_fact_recall():
    """
    Ticket #001 Test: Single fact query
    Expected: Returns specific fact when queried
    """
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)

    memory.memory_write(
        content="Research deadline is March 15 2026 unique7x4b",
        tier="l3",
        importance=5,
        category="research"
    )

    result = memory.memory_read(query="unique7x4b")

    print(f"  Single fact query result count: {len(result) if result else 0}")
    print(f"  Result: {result}")

    assert len(result) > 0, "Single fact query returned no results"
    found = any("March 15" in str(entry) for entry in result)
    assert found, f"Specific fact not found in query results. Got: {result}"

    print(f"  ✓ Single fact query successful")
    return True


def test_001_write_read_l3_vs_l2():
    """
    Ticket #001 Test: Both L3 and L2 writes are readable
    Expected: Items written to l3 and l2 both retrievable
    """
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)

    l3_content = "L3 write test unique9m2n"
    l2_content = "L2 write test unique9m2n"

    l3_result = memory.memory_write(content=l3_content, tier="l3", importance=3, category="test")
    l2_result = memory.memory_write(content=l2_content, tier="l2", importance=3, category="test")

    print(f"  L3 write result: {l3_result}")
    print(f"  L2 write result: {l2_result}")

    # Read L3
    l3_read = memory.memory_read(query="unique9m2n", tier="l3")
    print(f"  L3 read count: {len(l3_read) if l3_read else 0}")

    # Read L2
    l2_read = memory.memory_read(query="unique9m2n", tier="l2")
    print(f"  L2 read count: {len(l2_read) if l2_read else 0}")

    assert len(l3_read) > 0, f"L3 read returned empty. Write result was: {l3_result}"
    assert len(l2_read) > 0, f"L2 read returned empty. Write result was: {l2_result}"

    print(f"  ✓ Both L3 and L2 reads working")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("MEMORY SYSTEM TESTS - Ticket #001")
    print("=" * 60)

    runner.run_test(test_001_memory_write, "Memory Write L3", ticket_id=1)
    runner.run_test(test_001_memory_read_after_write, "Memory Read After Write", ticket_id=1)
    runner.run_test(test_001_memory_bulk_read, "Bulk Memory Read (empty query)", ticket_id=1)
    runner.run_test(test_001_single_fact_recall, "Single Fact Recall", ticket_id=1)
    runner.run_test(test_001_write_read_l3_vs_l2, "L3 and L2 Read/Write", ticket_id=1)

    runner.print_summary()
    runner.save_results("testing/results/memory_test_results.json")
    runner.generate_failure_tickets("testing/results/memory_failures.txt")
