"""
testing/test_memory_roundtrip.py

PHASE 3 CANARY TEST — Memory round-trip via the real Claude tool loop.

WARNING: COSTLY. Each run incurs ~4–8 paid Claude API calls (~$0.03–$0.08).
NOT included in run_all_tests.py. Invoke directly:

    python testing/test_memory_roundtrip.py

Reproduces or rules out the LOG1/LOG2 production failure mode:

  1. Claude in root mode must actually call memory_write (not mime it in text).
  2. Storage must survive a process teardown (agent #1 deleted, agent #2 created fresh).
  3. Agent #2 must retrieve the stored content via memory_read.
  4. The retrieved content must reach Claude's final text response.

Three possible outcomes (master prompt §6 Phase 3.1):

  GREEN: round-trip works end-to-end. Acceptance gate satisfied.
  RED on write : Claude not calling memory_write at all → prompt bug → Phase 5.
  RED on read  : write succeeds but new instance does not surface the content.
                 Diagnose via the ladder in master prompt §6 Phase 3.2.

The test cleans up its marker entries from Supabase at the end (best-effort).
"""
import builtins
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Block any input() prompts (e.g. _check_monthly_review) before importing PiAgent.
_real_input = builtins.input
builtins.input = lambda *args, **kwargs: "no"

from pi_agent import PiAgent  # noqa: E402
from app.config import SUPABASE_URL, SUPABASE_KEY  # noqa: E402
from supabase import create_client  # noqa: E402


COLOR = "purple"
MARKER = f"test_marker_{uuid.uuid4().hex[:8]}"
WRITE_MSG = f"Please remember the following exactly: {MARKER} is associated with the color {COLOR}."
READ_MSG = f"What color did I associate with {MARKER}?"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVOLUTION_LOG = os.path.join(REPO_ROOT, "logs", "evolution.jsonl")


def _read_log_entries_after(timestamp_iso: str):
    """Return evolution.jsonl entries logged after the given ISO timestamp."""
    if not os.path.exists(EVOLUTION_LOG):
        return []
    cutoff = datetime.fromisoformat(timestamp_iso)
    out = []
    with open(EVOLUTION_LOG, "r") as f:
        for line in f:
            try:
                e = json.loads(line.strip())
                if datetime.fromisoformat(e["timestamp"]) > cutoff:
                    out.append(e)
            except Exception:
                continue
    return out


def _print_tool_calls(prefix, entries):
    n_calls = 0
    for e in entries:
        for tc in e.get("tool_calls", []):
            n_calls += 1
            inp = tc.get("input", {})
            inp_str = json.dumps(inp)[:160]
            print(f"  {prefix} {tc.get('name')}({inp_str})")
    return n_calls


def _cleanup_marker(marker):
    """Remove the test entry so it doesn't pollute production memory. Best-effort."""
    try:
        c = create_client(SUPABASE_URL, SUPABASE_KEY)
        # L3
        r3 = c.table("l3_active_memory").delete().ilike("content", f"%{marker}%").execute()
        # L2 — title is a 100-char prefix of content, so marker may or may not be there
        r2 = c.table("organized_memory").delete().ilike("title", f"%{marker}%").execute()
        # L1
        r1 = c.table("raw_wiki").delete().ilike("content", f"%{marker}%").execute()
        print(f"  cleanup: l3={len(r3.data or [])} l2={len(r2.data or [])} l1={len(r1.data or [])}")
    except Exception as e:
        print(f"  cleanup non-fatal error: {e}")


def main():
    print("\n=== test_memory_roundtrip.py (COSTLY: ~$0.03–$0.08) ===\n")
    print(f"  marker:   {MARKER}")
    print(f"  color:    {COLOR}")
    print(f"  write msg: {WRITE_MSG}")
    print(f"  read msg:  {READ_MSG}")
    print()

    test_start_iso = datetime.now(timezone.utc).isoformat()
    findings = []
    write_calls = []
    color_in_response = False
    read_response = ""

    try:
        # ===== Phase A: write via tool loop in fresh PiAgent =====
        print("[A] Building PiAgent #1 (fresh process state)...")
        agent1 = PiAgent()
        agent1.mode = "root"
        a_session_id = agent1.session_id
        print(f"    session_id #1: {a_session_id}")
        print(f"    sending write message...")
        write_response = agent1.process_input(WRITE_MSG)
        print(f"    Pi #1 response (first 400 chars):")
        print(f"      {write_response[:400]}")

        # Settle log writes, then read what got logged for this session
        time.sleep(0.5)
        a_logs = [e for e in _read_log_entries_after(test_start_iso)
                  if e.get("session_id") == a_session_id]
        print(f"    {len(a_logs)} interaction(s) logged for session #1")
        a_tool_count = _print_tool_calls("    ↳ tool:", a_logs)

        a_tool_calls = [tc for e in a_logs for tc in e.get("tool_calls", [])]
        write_calls = [tc for tc in a_tool_calls if tc.get("name") == "memory_write"]
        print(f"    memory_write calls: {len(write_calls)}")

        # Tear down agent #1
        print()
        print("[B] Tearing down PiAgent #1 (simulating exit)...")
        del agent1

        # ===== Phase C: rebuild fresh PiAgent — simulating restart =====
        print()
        print("[C] Building PiAgent #2 (fresh process state, must re-sync L3 from Supabase)...")
        agent2 = PiAgent()
        agent2.mode = "root"
        b_session_id = agent2.session_id
        print(f"    session_id #2: {b_session_id}")
        if b_session_id == a_session_id:
            findings.append({
                "type": "WARNING",
                "issue": "Session IDs identical across restart",
                "evidence": f"agent1={a_session_id}, agent2={b_session_id}",
            })
        print(f"    sending recall message...")
        read_response = agent2.process_input(READ_MSG)
        print(f"    Pi #2 response (first 600 chars):")
        print(f"      {read_response[:600]}")

        time.sleep(0.5)
        b_logs = [e for e in _read_log_entries_after(test_start_iso)
                  if e.get("session_id") == b_session_id]
        print(f"    {len(b_logs)} interaction(s) logged for session #2")
        b_tool_count = _print_tool_calls("    ↳ tool:", b_logs)
        b_tool_calls = [tc for e in b_logs for tc in e.get("tool_calls", [])]

        # ===== Verdict computation =====
        color_in_response = COLOR.lower() in read_response.lower()
        marker_in_response = MARKER in read_response

        # Diagnosis ladder (only if we'll need it)
        if not color_in_response:
            ladder = []
            ladder.append({
                "rung": "1. write logged via tool loop?",
                "answer": f"{len(write_calls)} memory_write call(s) in session #1",
            })
            try:
                c = create_client(SUPABASE_URL, SUPABASE_KEY)
                r = c.table("l3_active_memory").select("*").ilike("content", f"%{MARKER}%").execute()
                ladder.append({
                    "rung": "2. present in supabase l3_active_memory?",
                    "answer": f"{len(r.data or [])} match(es); ids: {[row.get('id') for row in (r.data or [])]}",
                })
            except Exception as e:
                ladder.append({"rung": "2. supabase query", "answer": f"error: {e}"})
            try:
                import sqlite3
                conn = sqlite3.connect(agent2.memory.sqlite_path)
                rows = conn.execute(
                    "SELECT content FROM l3_cache WHERE content LIKE ?", [f"%{MARKER}%"]
                ).fetchall()
                conn.close()
                ladder.append({
                    "rung": "3. present in SQLite l3_cache (post-sync)?",
                    "answer": f"{len(rows)} row(s)",
                })
            except Exception as e:
                ladder.append({"rung": "3. sqlite query", "answer": f"error: {e}"})
            try:
                ctx = agent2.memory.get_l3_context()
                ladder.append({
                    "rung": "4. present in get_l3_context() string?",
                    "answer": ("yes" if MARKER in ctx else f"no (context len={len(ctx)})"),
                })
            except Exception as e:
                ladder.append({"rung": "4. get_l3_context", "answer": f"error: {e}"})
            read_calls = [tc for tc in b_tool_calls if tc.get("name") == "memory_read"]
            ladder.append({
                "rung": "5. memory_read called by Pi #2?",
                "answer": (
                    f"{len(read_calls)} call(s); queries: "
                    f"{[tc.get('input', {}).get('query') for tc in read_calls]}"
                ),
            })
            if read_calls:
                # rung 6: did the query match the stored content?
                queries = [tc.get("input", {}).get("query", "") for tc in read_calls]
                ladder.append({
                    "rung": "6. did query string overlap with stored content?",
                    "answer": (
                        f"queries={queries}; marker in any query={any(MARKER in q for q in queries)}"
                    ),
                })
            findings.append({
                "type": "RED on read",
                "issue": f"recall response did not contain '{COLOR}'",
                "diagnosis_ladder": ladder,
                "response_excerpt": read_response[:600],
            })

        # Cleanup
        print()
        print("[D] cleanup of marker entries from Supabase...")
        _cleanup_marker(MARKER)
        del agent2

    finally:
        builtins.input = _real_input

    # ===== Verdict =====
    print()
    print("=" * 72)
    if not write_calls:
        print("VERDICT: RED ON WRITE — Claude did not call memory_write")
        print()
        print(f"Pi #1 response excerpt:\n  {write_response[:500]}")
        print()
        print("Implication: prompt-engineering bug. The model is producing fluent prose")
        print("instead of issuing a tool_use block. Escalate to Phase 5.")
        return 1

    if not color_in_response:
        print(f"VERDICT: RED ON READ — write succeeded but recall did not surface '{COLOR}'")
        print()
        print("Diagnosis:")
        for finding in findings:
            for k, v in finding.items():
                if k == "diagnosis_ladder":
                    print("  diagnosis_ladder:")
                    for rung in v:
                        print(f"    - {rung['rung']:42s} {rung['answer']}")
                else:
                    print(f"  {k}: {str(v)[:300]}")
            print()
        return 2

    print("VERDICT: GREEN — round-trip works")
    print(f"  write: {len(write_calls)} memory_write call(s); marker in input: "
          f"{any(MARKER in str(tc.get('input', {})) for tc in write_calls)}")
    print(f"  read response contained '{COLOR}': {color_in_response}")
    print(f"  bonus — marker in response text: {marker_in_response}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
