"""
testing/test_l1_autolog.py — T-024: L1 (raw_wiki) auto-logging.

Verifies that MemoryTools.log_turn() correctly writes conversation turns to
raw_wiki in Supabase. Does NOT invoke PiAgent or any LLM API — tests only
the storage layer, so it is free to run.

Tests:
  1. A turn with no tools produces exactly 2 rows (user + assistant).
  2. A turn with tool calls produces user + tool-per-call + assistant rows.
  3. All rows share the same thread_id.
  4. turn_number and mode appear in metadata.
  5. memory_write(tier="l1") uses uuid5 thread derivation (not raw session_id).

Touches Supabase (no Claude API). Cleans up its own entries by thread_id.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import SUPABASE_URL, SUPABASE_KEY  # noqa: E402
from tools.tools_memory import MemoryTools          # noqa: E402
from supabase import create_client                  # noqa: E402

SESSION_ID = uuid.uuid4().hex[:8]
THREAD_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, SESSION_ID))
client = create_client(SUPABASE_URL, SUPABASE_KEY)
memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)


def _rows_for_thread(thread_id: str) -> list:
    r = (client.table("raw_wiki")
         .select("*")
         .eq("thread_id", thread_id)
         .order("timestamp")
         .execute())
    return r.data or []


def _cleanup(thread_id: str):
    try:
        client.table("raw_wiki").delete().eq("thread_id", thread_id).execute()
    except Exception as e:
        print(f"  cleanup non-fatal: {e}")


def main():
    print("\n=== test_l1_autolog.py ===\n")
    print(f"  session_id : {SESSION_ID}")
    print(f"  thread_id  : {THREAD_ID}")
    print()
    failed = []

    # ------------------------------------------------------------------ #
    # Test 1: no-tool turn -> 2 rows (user + assistant)
    # ------------------------------------------------------------------ #
    t1_thread = str(uuid.uuid5(uuid.NAMESPACE_DNS, uuid.uuid4().hex[:8]))
    print("[1] log_turn with no tools -> expect 2 rows...")
    result = memory.log_turn(
        thread_id=t1_thread,
        session_id=SESSION_ID,
        turn_number=1,
        user_content="Hello Pi",
        assistant_content="Hello Ash, how can I help?",
        mode="normie",
    )
    print(f"    result: {result}")
    if not result.get("success"):
        failed.append(f"Test 1: log_turn returned success=False: {result}")
    elif result["rows"] != 2:
        failed.append(f"Test 1: expected 2 rows, got {result['rows']}")
    else:
        rows = _rows_for_thread(t1_thread)
        if len(rows) != 2:
            failed.append(f"Test 1: Supabase returned {len(rows)} rows, expected 2")
        else:
            roles = [r["role"] for r in rows]
            if roles != ["user", "assistant"]:
                failed.append(f"Test 1: unexpected role order {roles}")
            else:
                print(f"    [OK] 2 rows, roles={roles}")
    _cleanup(t1_thread)

    # ------------------------------------------------------------------ #
    # Test 2: turn with 2 tool calls -> 4 rows (user + tool + tool + assistant)
    # ------------------------------------------------------------------ #
    t2_thread = str(uuid.uuid5(uuid.NAMESPACE_DNS, uuid.uuid4().hex[:8]))
    print()
    print("[2] log_turn with 2 tool calls -> expect 4 rows...")
    tool_calls = [
        {"name": "memory_read",  "input": {"query": "subway"},    "result_summary": "[2 results]"},
        {"name": "memory_write", "input": {"content": "oregano"}, "result_summary": '{"success": true}'},
    ]
    result = memory.log_turn(
        thread_id=t2_thread,
        session_id=SESSION_ID,
        turn_number=2,
        user_content="Remember I like oregano bread.",
        assistant_content="Stored: oregano bread preference.",
        mode="root",
        tool_calls=tool_calls,
        tokens_in=120,
        tokens_out=40,
        cost=0.000256,
    )
    print(f"    result: {result}")
    if not result.get("success"):
        failed.append(f"Test 2: log_turn returned success=False: {result}")
    elif result["rows"] != 4:
        failed.append(f"Test 2: expected 4 rows, got {result['rows']}")
    else:
        rows = _rows_for_thread(t2_thread)
        if len(rows) != 4:
            failed.append(f"Test 2: Supabase returned {len(rows)} rows, expected 4")
        else:
            roles = [r["role"] for r in rows]
            if roles != ["user", "tool", "tool", "assistant"]:
                failed.append(f"Test 2: unexpected role order {roles}")
            else:
                # Verify tool_name in metadata
                tool_rows = [r for r in rows if r["role"] == "tool"]
                tool_names = [r["metadata"].get("tool_name") for r in tool_rows]
                if tool_names != ["memory_read", "memory_write"]:
                    failed.append(f"Test 2: tool_name metadata wrong: {tool_names}")
                else:
                    print(f"    [OK] 4 rows, roles={roles}, tool_names={tool_names}")
    _cleanup(t2_thread)

    # ------------------------------------------------------------------ #
    # Test 3: all rows share thread_id; metadata has session_id, turn, mode
    # ------------------------------------------------------------------ #
    t3_thread = str(uuid.uuid5(uuid.NAMESPACE_DNS, uuid.uuid4().hex[:8]))
    t3_session = uuid.uuid4().hex[:8]
    print()
    print("[3] metadata integrity (thread_id, session_id, turn, mode)...")
    memory.log_turn(
        thread_id=t3_thread,
        session_id=t3_session,
        turn_number=7,
        user_content="test",
        assistant_content="ok",
        mode="root",
        tokens_in=10,
        tokens_out=5,
        cost=0.000012,
    )
    rows = _rows_for_thread(t3_thread)
    if len(rows) != 2:
        failed.append(f"Test 3: expected 2 rows, got {len(rows)}")
    else:
        for r in rows:
            if r["thread_id"] != t3_thread:
                failed.append(f"Test 3: thread_id mismatch on role={r['role']}")
            meta = r.get("metadata", {})
            if meta.get("session_id") != t3_session:
                failed.append(f"Test 3: session_id wrong in {r['role']} metadata: {meta}")
            if meta.get("turn") != 7:
                failed.append(f"Test 3: turn wrong in {r['role']} metadata: {meta}")
            if meta.get("mode") != "root":
                failed.append(f"Test 3: mode wrong in {r['role']} metadata: {meta}")
        if not failed:
            # assistant row should carry token + cost fields
            asst = next(r for r in rows if r["role"] == "assistant")
            m = asst["metadata"]
            if m.get("tokens_in") != 10 or m.get("tokens_out") != 5:
                failed.append(f"Test 3: token metadata wrong: {m}")
            else:
                print(f"    [OK] metadata integrity confirmed on both rows")
    _cleanup(t3_thread)

    # ------------------------------------------------------------------ #
    # Test 4: memory_write(tier="l1") thread derivation uses uuid5
    # ------------------------------------------------------------------ #
    t4_session = uuid.uuid4().hex[:8]
    expected_thread = str(uuid.uuid5(uuid.NAMESPACE_DNS, t4_session))
    print()
    print(f"[4] memory_write(tier='l1') uuid5 thread derivation...")
    r = memory.memory_write(
        content="L1 explicit write test",
        tier="l1",
        importance=3,
        category="test_l1",
        session_id=t4_session,
    )
    print(f"    write result: {r}")
    if not r.get("success"):
        failed.append(f"Test 4: memory_write(tier='l1') failed: {r}")
    else:
        rows = _rows_for_thread(expected_thread)
        if not any(expected_thread == row["thread_id"] for row in rows):
            failed.append(
                f"Test 4: no row found with expected thread_id={expected_thread}; "
                f"got {[row['thread_id'] for row in rows]}"
            )
        else:
            row = next(r2 for r2 in rows if r2["thread_id"] == expected_thread)
            if row["metadata"].get("session_id") != t4_session:
                failed.append(f"Test 4: session_id missing from metadata: {row['metadata']}")
            else:
                print(f"    [OK] thread_id={expected_thread[:18]}... session_id in metadata")
        _cleanup(expected_thread)

    # ------------------------------------------------------------------ #
    # Results
    # ------------------------------------------------------------------ #
    print()
    print("=" * 60)
    if failed:
        print(f"FAILED: {len(failed)} assertion(s)")
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)
    print("[OK] All assertions passed - L1 auto-logging works correctly.")
    sys.exit(0)


if __name__ == "__main__":
    main()
