"""scripts/seed_identity_facts.py — T-282: seed the eligibility-relevant
identity facts that were missing from L3 permanent_profile.

Diagnosed 2026-07-07: a real "scholarships matching my profile" ask got
answered from a profile containing Subway/McDonald's orders and manhwa
favorites — no F-1 status, no gender, no "CS undergrad at GSU" — because
those facts were never written. Fixing the behavior (T-281) can't help if
the facts it's supposed to apply don't exist.

This is a WRITE to production Supabase (via the real memory_write path —
verified, not hand-inserted SQL). Run manually, once, when ready:

    python scripts/seed_identity_facts.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.tools_memory import MemoryTools

# T-282: the specific facts diagnosed as missing/vague during the scholarship
# failure. Not a full identity reconstruction — just what decided eligibility
# and was absent. "stated" because Ash has confirmed each in conversation.
IDENTITY_FACTS = [
    "Ash is male.",
    "Ash is an F-1 international student (visa status) — not a US citizen or permanent resident, so residency-restricted aid (e.g. state-resident-only scholarships) does not apply.",
    "Ash is a CS undergrad at Georgia State University (GSU), researching graph neural networks (GNN).",
]


def seed(memory: MemoryTools) -> list:
    results = []
    for fact in IDENTITY_FACTS:
        r = memory.memory_write(
            content=fact, tier="l3", importance=10,
            category="permanent_profile", source="stated",
        )
        results.append({"fact": fact, "result": r})
    return results


def main():
    memory = MemoryTools()
    results = seed(memory)
    for r in results:
        ok = r["result"].get("success")
        print(f"  {'OK' if ok else 'FAIL'}: {r['fact'][:70]}")
    failed = [r for r in results if not r["result"].get("success")]
    if failed:
        print(f"\n{len(failed)} write(s) failed — see output above.")
        sys.exit(1)
    print(f"\nSeeded {len(results)} identity facts to permanent_profile (importance 10).")


if __name__ == "__main__":
    main()
