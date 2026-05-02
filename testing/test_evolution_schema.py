"""
testing/test_evolution_schema.py

Reproduction + regression test for SM-001:
- evolution.log_interaction writes the field `tools_used` (list of name strings)
- evolution.analyze_performance reads the field `tool_calls` (list of dicts)
- The two field names never agree, so tool_usage and tool_success_rates are
  silently empty dicts forever.

This file holds POST-FIX expectations:
- log_interaction also writes `tool_calls` (the structured list)
- log_interaction also writes a top-level `session_id` (extracted from metadata)
- analyze_performance returns a populated `tool_usage`, `tool_success_rates`,
  and a per-session breakdown under `sessions`

Run directly:
    python testing/test_evolution_schema.py

Exit codes:
  0 — all tests passed (post-fix state)
  1 — assertion failure (bug present, or fix incomplete)
  2 — unexpected error (test infrastructure broke)

The test does NOT touch Supabase, Claude, Groq, or any paid API. It uses
a tempdir for log files. Safe to run in any environment with the repo's
Python deps installed.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution import EvolutionTracker  # noqa: E402


def _log_three_interactions(tracker):
    """Log 1 success / 1 failure / 1 mixed-tools interaction with realistic shape."""
    tracker.log_interaction(
        user_input="what's my deadline?",
        pi_response="March 15.",
        tool_calls=[
            {"id": "1", "name": "memory_read", "input": {"query": "deadline"}}
        ],
        success=True,
        mode="root",
        cost=0.005,
        model="claude-sonnet-4-6",
        tokens_in=100,
        tokens_out=20,
        metadata={"session_id": "test-001"},
    )
    tracker.log_interaction(
        user_input="remember X",
        pi_response="failed.",
        tool_calls=[
            {"id": "2", "name": "memory_write", "input": {"content": "X"}}
        ],
        success=False,
        mode="root",
        cost=0.003,
        model="claude-sonnet-4-6",
        tokens_in=80,
        tokens_out=15,
        metadata={"session_id": "test-001"},
    )
    tracker.log_interaction(
        user_input="run X then save",
        pi_response="done.",
        tool_calls=[
            {"id": "3", "name": "execute_python", "input": {"code": "..."}},
            {"id": "4", "name": "memory_write", "input": {"content": "..."}},
        ],
        success=True,
        mode="root",
        cost=0.008,
        model="claude-sonnet-4-6",
        tokens_in=200,
        tokens_out=50,
        metadata={"session_id": "test-002"},
    )


def test_tool_usage_populated():
    """
    Headline assertion for SM-001: after logging 3 interactions with tool_calls,
    analyze_performance must return non-empty tool_usage and tool_success_rates.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "evolution_test.jsonl")
        tracker = EvolutionTracker(log_path=log_path)

        _log_three_interactions(tracker)
        analysis = tracker.analyze_performance(days=7)

        assert "error" not in analysis, f"got error: {analysis}"
        assert analysis["total_interactions"] == 3, f"want 3, got {analysis['total_interactions']}"
        assert analysis["successful"] == 2, f"want 2 successful, got {analysis['successful']}"
        assert analysis["failed"] == 1, f"want 1 failed, got {analysis['failed']}"

        tu = analysis["tool_usage"]
        assert tu, (
            "tool_usage is empty — SM-001 (write 'tools_used' / read 'tool_calls' "
            f"drift) not fixed. analysis: {analysis}"
        )
        assert tu.get("memory_read") == 1, f"memory_read count want 1, got {tu.get('memory_read')}"
        assert tu.get("memory_write") == 2, f"memory_write count want 2, got {tu.get('memory_write')}"
        assert tu.get("execute_python") == 1, f"execute_python count want 1, got {tu.get('execute_python')}"

        tsr = analysis["tool_success_rates"]
        assert tsr, f"tool_success_rates is empty: {analysis}"
        # memory_read appeared in 1 successful interaction → 100%
        assert tsr["memory_read"] == 1.0, f"memory_read rate want 1.0, got {tsr['memory_read']}"
        # memory_write appeared in 1 failed + 1 successful interaction → 50%
        assert tsr["memory_write"] == 0.5, f"memory_write rate want 0.5, got {tsr['memory_write']}"
        # execute_python appeared in 1 successful → 100%
        assert tsr["execute_python"] == 1.0, f"execute_python rate want 1.0, got {tsr['execute_python']}"

        print(f"  tool_usage = {tu}")
        print(f"  tool_success_rates = {tsr}")


def test_session_id_top_level():
    """
    SM-001 part 2: session_id must appear at the top level of each log entry,
    not buried inside metadata.session_id. This makes per-session log queries trivial.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "evolution_session.jsonl")
        tracker = EvolutionTracker(log_path=log_path)

        tracker.log_interaction(
            user_input="hi",
            pi_response="hello",
            tool_calls=[],
            success=True,
            mode="normie",
            metadata={"session_id": "abc12345"},
        )

        with open(log_path) as f:
            entry = json.loads(f.readline())

        assert "session_id" in entry, (
            f"session_id should be top-level, not buried in metadata. entry keys: {list(entry.keys())}"
        )
        assert entry["session_id"] == "abc12345", (
            f"session_id mismatch: want 'abc12345', got '{entry.get('session_id')}'"
        )
        # Backward compat: metadata.session_id can stay
        assert entry.get("metadata", {}).get("session_id") == "abc12345"
        print(f"  session_id at top level: {entry['session_id']!r}")


def test_per_session_breakdown():
    """
    Master prompt §6 Phase 2 step 6: analyze_performance should expose per-session
    breakdowns so 'what did session X do' is queryable from the analytics output.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "evolution_sessions.jsonl")
        tracker = EvolutionTracker(log_path=log_path)

        _log_three_interactions(tracker)
        analysis = tracker.analyze_performance(days=7)

        sessions = analysis.get("sessions", {})
        assert sessions, f"analysis has no 'sessions' key: {analysis}"

        # session test-001 had 1 success + 1 failure, total cost 0.008
        s1 = sessions.get("test-001")
        assert s1 is not None, f"missing session test-001: {sessions}"
        assert s1["interactions"] == 2, f"test-001 interactions want 2, got {s1['interactions']}"
        assert s1["successful"] == 1, f"test-001 successful want 1, got {s1['successful']}"
        assert s1["failed"] == 1, f"test-001 failed want 1, got {s1['failed']}"
        assert abs(s1["cost"] - 0.008) < 1e-6, f"test-001 cost want 0.008, got {s1['cost']}"

        # session test-002 had 1 success
        s2 = sessions.get("test-002")
        assert s2 is not None, f"missing session test-002: {sessions}"
        assert s2["interactions"] == 1
        assert s2["successful"] == 1
        assert s2["failed"] == 0
        assert abs(s2["cost"] - 0.008) < 1e-6, f"test-002 cost want 0.008, got {s2['cost']}"

        print(f"  sessions = {sessions}")


def test_legacy_log_entries_still_analyzable():
    """
    Forward fix should remain compatible with old entries that only have
    `tools_used` (no `tool_calls`) and only `metadata.session_id` (no top-level).
    Older logs/evolution.jsonl entries from before the fix must still produce
    sensible analytics so we don't lose history at the upgrade boundary.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "evolution_legacy.jsonl")
        # Hand-write a "legacy" entry (the shape current production logs have):
        legacy = {
            "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "mode": "root",
            "model": "claude-sonnet-4-6",
            "success": True,
            "cost": 0.004,
            "tokens_in": 100,
            "tokens_out": 20,
            "tools_used": ["memory_read", "memory_read"],
            "user_message_length": 20,
            "response_length": 50,
            "metadata": {"duration_seconds": 1.0, "session_id": "legacy-1"},
        }
        with open(log_path, "w") as f:
            f.write(json.dumps(legacy) + "\n")

        tracker = EvolutionTracker(log_path=log_path)
        analysis = tracker.analyze_performance(days=7)

        tu = analysis["tool_usage"]
        assert tu.get("memory_read") == 2, (
            f"legacy entry should still contribute to tool_usage via tools_used fallback. got: {tu}"
        )
        # session_id from metadata should still surface in per-session breakdown
        sessions = analysis.get("sessions", {})
        assert "legacy-1" in sessions, (
            f"legacy session_id (in metadata only) should still appear in sessions: {sessions}"
        )
        print(f"  legacy entry analyzed: tool_usage={tu}, sessions={list(sessions.keys())}")


def main():
    tests = [
        ("tool_usage populated after logging tool calls", test_tool_usage_populated),
        ("session_id at top level of log entry",          test_session_id_top_level),
        ("per-session breakdown in analyze_performance",  test_per_session_breakdown),
        ("legacy log entries still analyzable",           test_legacy_log_entries_still_analyzable),
    ]
    print("\n=== test_evolution_schema.py ===\n")
    failed = []
    for name, fn in tests:
        print(f"[*] {name} ...")
        try:
            fn()
            print(f"    PASSED\n")
        except AssertionError as e:
            print(f"    FAILED: {e}\n")
            failed.append((name, "FAILED", str(e)))
        except Exception as e:
            import traceback
            print(f"    ERROR: {e}")
            traceback.print_exc()
            print()
            failed.append((name, "ERROR", str(e)))
    print("=" * 60)
    if failed:
        print(f"{len(failed)} of {len(tests)} tests failed:")
        for name, status, msg in failed:
            print(f"  - {status}: {name}")
            print(f"      {msg[:200]}")
        sys.exit(1)
    print(f"All {len(tests)} tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
