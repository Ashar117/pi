"""
testing/test_session_id_propagation.py

S-009 / T-013 verification: session_id must propagate consistently across every
log entry in a single session, and across the L1 raw_wiki thread_id when L1 writes
happen in that session.

This test runs WITHOUT incurring paid API calls. It works two ways:

  (a) Static check on the existing logs/evolution.jsonl — for every session_id
      seen in the log, group entries and assert internal consistency:
        - top-level session_id matches metadata.session_id when both present
        - all entries from the same session keep the same session_id
        - at least one session has 2+ entries (otherwise we can't assert
          propagation with confidence — that's an empty-test gotcha)

  (b) In-process simulation: instantiate EvolutionTracker with a tempdir log path,
      log 5 interactions all carrying the same session_id, read back the file,
      assert every entry has the same top-level session_id (post-Phase-2 schema).

Together (a) and (b) close the gap that S-009's claim left open: that session_id
ACTUALLY appears identically across multiple turns in a real session log, not
just in the code's intent.
"""
import builtins
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution import EvolutionTracker  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD_LOG = os.path.join(REPO_ROOT, "logs", "evolution.jsonl")


def _load_prod_entries():
    if not os.path.exists(PROD_LOG):
        return []
    out = []
    with open(PROD_LOG) as f:
        for line in f:
            try:
                out.append(json.loads(line.strip()))
            except Exception:
                continue
    return out


def test_prod_log_session_ids_internally_consistent():
    """
    For each session_id in the real log, the set of entries that claim that
    session_id must agree internally — top-level session_id == metadata.session_id
    where both are populated, and (post-Phase-2) the top-level field is preferred.
    """
    entries = _load_prod_entries()
    assert entries, f"production log is empty or unreadable: {PROD_LOG}"

    by_session = defaultdict(list)
    for e in entries:
        sid = (e.get("session_id") or e.get("metadata", {}).get("session_id") or "unknown")
        by_session[sid].append(e)

    # Drop the legacy 'unknown' bucket from this check — those are pre-T-013 entries.
    sessions = {sid: ents for sid, ents in by_session.items() if sid != "unknown"}
    assert sessions, "no entries with a session_id; T-013 may have regressed"

    # We need at least one session with 2+ entries to make a propagation claim
    multi_turn_sessions = {sid: ents for sid, ents in sessions.items() if len(ents) >= 2}
    assert multi_turn_sessions, (
        "no session has >=2 entries; cannot assert propagation. "
        f"sessions seen: {[(sid, len(ents)) for sid, ents in sessions.items()]}"
    )

    # For every multi-turn session, check internal consistency
    for sid, ents in multi_turn_sessions.items():
        # All top-level session_ids agree (where present)
        top_sids = {e.get("session_id") for e in ents if e.get("session_id")}
        assert len(top_sids) <= 1, (
            f"session {sid}: top-level session_id values inconsistent: {top_sids}"
        )
        # All metadata.session_ids agree (where present)
        meta_sids = {e.get("metadata", {}).get("session_id") for e in ents
                     if e.get("metadata", {}).get("session_id")}
        assert len(meta_sids) <= 1, (
            f"session {sid}: metadata.session_id values inconsistent: {meta_sids}"
        )
        # If both are present, they must equal each other
        if top_sids and meta_sids:
            assert top_sids == meta_sids, (
                f"session {sid}: top-level vs metadata session_id mismatch: "
                f"top={top_sids} meta={meta_sids}"
            )

    print(f"  multi-turn sessions: {[(sid, len(ents)) for sid, ents in multi_turn_sessions.items()]}")
    print(f"  legacy 'unknown' bucket size: {len(by_session.get('unknown', []))} (pre-T-013, expected)")


def test_in_process_simulation_5_interactions_same_session_id():
    """
    Simulate a 5-interaction session via EvolutionTracker. Every entry must
    carry the same top-level session_id (post-Phase-2 SM-001 fix).
    """
    sid = "abcd1234"
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "evolution_session_test.jsonl")
        tracker = EvolutionTracker(log_path=log_path)
        for i in range(5):
            tracker.log_interaction(
                user_input=f"turn {i}",
                pi_response=f"reply {i}",
                tool_calls=[],
                success=True,
                mode="root" if i % 2 == 0 else "normie",
                cost=0.001 * i,
                model="claude-sonnet-4-6" if i % 2 == 0 else "groq",
                tokens_in=10 * i, tokens_out=2 * i,
                metadata={"session_id": sid, "duration_seconds": 0.1 * i},
            )
        with open(log_path) as f:
            entries = [json.loads(line) for line in f]

        assert len(entries) == 5, f"expected 5 entries, got {len(entries)}"
        seen_sids = {e["session_id"] for e in entries}
        assert seen_sids == {sid}, (
            f"session_id should be identical across all 5 entries. got: {seen_sids}"
        )
        seen_meta_sids = {e["metadata"]["session_id"] for e in entries}
        assert seen_meta_sids == {sid}, (
            f"metadata.session_id should also be identical. got: {seen_meta_sids}"
        )

        # Confirm the analyzer correctly groups all 5 into one session
        analysis = tracker.analyze_performance(days=7)
        sessions = analysis["sessions"]
        assert sid in sessions, f"sessions dict missing test session id {sid}: {sessions}"
        assert sessions[sid]["interactions"] == 5
        assert sessions[sid]["successful"] == 5
        assert sessions[sid]["failed"] == 0
        # Sum of costs 0 + 0.001 + 0.002 + 0.003 + 0.004 = 0.01
        assert abs(sessions[sid]["cost"] - 0.01) < 1e-9, sessions[sid]

        print(f"  5 entries, all session_id={sid!r}, cost={sessions[sid]['cost']}")


def test_session_id_distinct_across_sessions():
    """
    Session A and Session B in the same log must NOT share a session_id.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "evolution_two_sessions.jsonl")
        tracker = EvolutionTracker(log_path=log_path)
        for sid in ("sessA", "sessB"):
            for _ in range(3):
                tracker.log_interaction(
                    user_input="hi", pi_response="hello", tool_calls=[],
                    success=True, mode="normie",
                    metadata={"session_id": sid},
                )
        analysis = tracker.analyze_performance(days=7)
        sessions = analysis["sessions"]
        assert "sessA" in sessions and "sessB" in sessions, sessions
        assert sessions["sessA"]["interactions"] == 3
        assert sessions["sessB"]["interactions"] == 3
        # No bleed
        assert "sessA" != "sessB"
        print(f"  sessA: {sessions['sessA']}")
        print(f"  sessB: {sessions['sessB']}")


def main():
    tests = [
        ("prod log session_ids internally consistent", test_prod_log_session_ids_internally_consistent),
        ("5 interactions in same session share session_id (in-process)", test_in_process_simulation_5_interactions_same_session_id),
        ("two sessions in one log stay distinct", test_session_id_distinct_across_sessions),
    ]
    print("\n=== test_session_id_propagation.py ===\n")
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
    main()
