"""
testing/test_l2_content_search.py

SM-003 reproduction + regression test:

L2 (organized_memory) entries store the actual content under content.text (a JSONB
field). The current memory_read(tier="l2") filter only does ilike("title", ...),
where title is a 100-char prefix of content. Distinctive content past the first
100 chars is unreachable via L2 search. The chat-log production failure mode is
the same shape: write succeeds, recall returns empty because the query doesn't
match the title bucket.

This test deliberately writes an L2 entry whose UNIQUE keyword sits past the
100-char title prefix, then searches for that keyword. Pre-fix this returns 0
results; post-fix it should return the entry.

Touches Supabase (free, no Claude API). Cleans up its own entries.
"""
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import SUPABASE_URL, SUPABASE_KEY  # noqa: E402
from tools.tools_memory import MemoryTools  # noqa: E402
from supabase import create_client  # noqa: E402


# Build a content string that is >100 chars where the unique keyword lives in
# the BODY, not the title. Title = content[:100], so the keyword must appear
# only after position 100 to fail title-only search.
MARKER = f"L2BODYKW{uuid.uuid4().hex[:8].upper()}"
PADDING = (
    "this is a long L2 entry whose first hundred characters are deliberately "
    "filler text that does not contain the marker word "  # ~120 chars so far
)
CONTENT = f"{PADDING} {MARKER} appears here as the distinctive keyword in the body of the entry."

assert MARKER not in CONTENT[:100], (
    f"Marker '{MARKER}' must NOT appear in first 100 chars (the title). "
    f"first100={CONTENT[:100]!r}"
)
assert MARKER in CONTENT, "Marker must appear somewhere in content (sanity)"


def _cleanup(client, marker):
    """Best-effort cleanup of test entries."""
    try:
        # delete any L2 entries whose content.text contains the marker
        # (this requires a JSON filter — fall back to a wide fetch + python filter)
        try:
            client.table("organized_memory").delete().like("content->>text", f"%{marker}%").execute()
        except Exception:
            r = client.table("organized_memory").select("id, content").limit(100).order("created_at", desc=True).execute()
            stale_ids = [
                row["id"] for row in (r.data or [])
                if isinstance(row.get("content"), dict)
                and marker in str(row["content"].get("text", ""))
            ]
            for sid in stale_ids:
                client.table("organized_memory").delete().eq("id", sid).execute()
    except Exception as e:
        print(f"  cleanup non-fatal: {e}")


def main():
    print("\n=== test_l2_content_search.py ===\n")
    print(f"  marker (in body, not title): {MARKER}")
    print(f"  title (first 100 chars):     {CONTENT[:100]!r}")
    print(f"  marker in title? {MARKER in CONTENT[:100]} (expected False)")
    print()

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)
    failed = []

    try:
        # Write the entry to L2
        print("[1] writing L2 entry with marker in body...")
        write_result = memory.memory_write(
            content=CONTENT,
            tier="l2",
            importance=5,
            category="test_l2_content",
        )
        print(f"    write result: {write_result}")
        assert write_result.get("success") is True, f"L2 write failed: {write_result}"

        # Sanity: confirm via direct Supabase query that it's in there with the
        # marker hidden in content.text and not in title
        time.sleep(0.3)
        direct = client.table("organized_memory").select("*").eq("id", write_result["id"]).execute()
        assert direct.data and len(direct.data) == 1
        row = direct.data[0]
        assert MARKER not in (row.get("title") or ""), (
            f"marker leaked into title — test setup invalid: {row.get('title')!r}"
        )
        assert MARKER in str((row.get("content") or {}).get("text", "")), (
            f"marker not in content.text — Supabase write didn't land: {row.get('content')!r}"
        )
        print(f"    direct supabase confirm: id={row['id'][:8]}... title len={len(row.get('title',''))}")

        # Now search for the marker via memory_read(tier="l2") — this is the
        # path Pi takes when Claude calls memory_read.
        print()
        print("[2] memory_read(tier='l2', query=MARKER) — must return the entry...")
        results = memory.memory_read(query=MARKER, tier="l2")
        print(f"    got {len(results)} result(s)")
        for r in results[:3]:
            content = r.get("content")
            content_str = str(content)[:200] if content else ""
            print(f"      - id={r.get('id', '')[:8]}... title={(r.get('title') or '')[:60]!r}")

        if not any(r.get("id") == write_result["id"] for r in results):
            failed.append(
                f"L2 content search did not return the entry. "
                f"got {len(results)} result(s); none matched id={write_result['id']}"
            )

        # Also test tier=None — but per T-017 conservative fix, tier=None searches L3+L2.
        # So tier=None should also find the entry.
        print()
        print("[3] memory_read(tier=None, query=MARKER) — should also find via L2 branch...")
        results2 = memory.memory_read(query=MARKER, tier=None)
        print(f"    got {len(results2)} result(s)")
        if not any(r.get("id") == write_result["id"] for r in results2):
            failed.append(
                f"tier=None search (which includes L2) did not return the entry. "
                f"got {len(results2)} result(s)"
            )

    finally:
        # cleanup
        print()
        print("[4] cleanup...")
        _cleanup(client, MARKER)

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED: {len(failed)} assertion(s)")
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)
    print("All assertions passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
