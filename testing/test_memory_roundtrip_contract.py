"""C1 Phase A — memory round-trip contract (offline, free).

Guards the project's #1 recurring bug class: write-path / read-path divergence
(data stored one way, read another). The keystone T-148 bug was exactly that.
These assert that what is WRITTEN to a tier can be READ back with content intact,
exercising the real public MemoryTools API against an isolated SQLite db and a
no-network Supabase. No API cost.

Covered:
  - L3 (SQLite l3_cache): memory_write(tier="l3") -> memory_read(tier="l3")
  - L1 (raw_wiki) serialization: log_turn() builds rows that preserve user +
    assistant content (asserted via a recording Supabase double).

L2 (organized_memory) round-trips require real Supabase and live in the costly
suite; they are intentionally out of this free contract.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools  # noqa: E402


def _offline_mt(tmp_path):
    """Real MemoryTools with an isolated SQLite db and no network.

    Empty creds -> __init__ installs the _NoopSupabase shim, so L3 writes hit
    SQLite only and L1/L2 Supabase calls are silently dropped.
    """
    return MemoryTools(supabase_url="", supabase_key="",
                       sqlite_path=str(tmp_path / "pi.db"))


# ── L3: write -> read is content-preserving ──────────────────────────────────

# Note: we do NOT assert memory_write()["success"]. Offline, _verify_write checks
# the Supabase replica (a no-op here) and reports success=False even when the
# SQLite l3_cache row landed. The round-trip CONTRACT is read-back: if the write
# truly failed to persist, memory_read returns nothing and the test fails.

def test_l3_roundtrip_single_fact(tmp_path):
    mt = _offline_mt(tmp_path)
    mt.memory_write(content="the project codename is BLUEHERON",
                    tier="l3", category="note", importance=8)
    hits = mt.memory_read(query="BLUEHERON", tier="l3")
    assert any("BLUEHERON" in h["content"] for h in hits), (
        f"fact written to L3 was not readable back by its keyword; got {hits}"
    )


def test_l3_roundtrip_multiple_distinct_facts(tmp_path):
    mt = _offline_mt(tmp_path)
    facts = {
        "ZEBRAFISH": "my cat's name is ZEBRAFISH",
        "TANGERINE": "the deadline codeword is TANGERINE",
        "OBSIDIAN9": "the build tag is OBSIDIAN9",
    }
    for text in facts.values():
        mt.memory_write(content=text, tier="l3", category="note", importance=7)
    for kw in facts:
        hits = mt.memory_read(query=kw, tier="l3")
        assert any(kw in h["content"] for h in hits), f"L3 lost fact {kw!r}"


def test_l3_roundtrip_keyword_past_100_chars(tmp_path):
    """A distinctive token deep in the body must still be findable — guards the
    old 'search only the first 100 chars / title' divergence (SM-003 class)."""
    mt = _offline_mt(tmp_path)
    body = "context " * 30 + "SENTINELWORD42 at the very end"
    mt.memory_write(content=body, tier="l3", category="note", importance=6)
    hits = mt.memory_read(query="SENTINELWORD42", tier="l3")
    assert any("SENTINELWORD42" in h["content"] for h in hits), (
        "L3 search missed a keyword past char 100 — write/read divergence"
    )


# ── L1: log_turn serializes user + assistant content without loss ─────────────

class _RecordingSupabase:
    """Captures rows handed to .insert() so the L1 write shape can be asserted
    without a network call. _mock_name makes log_turn's pytest write-guard pass
    the call through instead of skipping it."""
    _mock_name = "recording"

    def __init__(self):
        self.inserted = []
        self._t = None

    def table(self, name):
        self._t = name
        return self

    def insert(self, rows):
        self.inserted.append((self._t, rows))
        return self

    def execute(self):
        return type("_R", (), {"data": []})()


def test_l1_log_turn_preserves_user_and_assistant(tmp_path):
    mt = _offline_mt(tmp_path)
    rec = _RecordingSupabase()
    mt.supabase = rec  # setter stores into _supabase_client; overrides the noop

    out = mt.log_turn(
        thread_id=str(uuid.uuid4()),
        session_id="abcd1234",
        turn_number=1,
        user_content="remember my codename is BLUEHERON",
        assistant_content="Noted - your codename is BLUEHERON.",
        mode="root",
    )
    assert out.get("success"), f"log_turn failed: {out}"
    assert rec.inserted, "log_turn wrote no rows to L1"
    table, rows = rec.inserted[0]
    assert table == "raw_wiki"
    by_role = {r["role"]: r["content"] for r in rows}
    assert "BLUEHERON" in by_role.get("user", ""), "L1 dropped the user's content"
    assert "BLUEHERON" in by_role.get("assistant", ""), "L1 dropped the assistant's content"


def test_l1_log_turn_orders_user_first_assistant_last(tmp_path):
    mt = _offline_mt(tmp_path)
    rec = _RecordingSupabase()
    mt.supabase = rec
    mt.log_turn(thread_id=str(uuid.uuid4()), session_id="sess0001", turn_number=2,
                user_content="hi", assistant_content="hello", mode="normie")
    _, rows = rec.inserted[0]
    roles = [r["role"] for r in rows]
    assert roles[0] == "user" and roles[-1] == "assistant", (
        f"L1 turn ordering broken: {roles}"
    )
