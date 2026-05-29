"""T-093 — Backfill embeddings for pre-T-080 L2 rows.

Usage:
    python scripts/backfill_l2_embeddings.py [--dry-run]

Reads every organized_memory row where content->metadata->embedding is NULL,
computes a Gemini embedding (same model as T-080), writes it back.
Idempotent — rows with existing embeddings are skipped. Resumable — kill and
restart; processed rows won't be touched again.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Resolve repo root so imports work when run from any cwd
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from memory.semantic_dedup import get_embedding


def _supabase_client():
    from supabase import create_client
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


def run(dry_run: bool = False):
    sb = _supabase_client()

    # Fetch all active L2 rows
    resp = sb.table("organized_memory").select("id,content").eq("status", "active").execute()
    rows = resp.data or []

    needs_embed = []
    for row in rows:
        content = row.get("content") or {}
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                content = {}
        meta = content.get("metadata") or {}
        if not meta.get("embedding"):
            text = content.get("text") or ""
            if text.strip():
                needs_embed.append((row["id"], text))

    total = len(rows)
    skip = total - len(needs_embed)
    print(f"[backfill] {total} rows total | {skip} already have embeddings | {len(needs_embed)} need embedding")

    if dry_run:
        print("[backfill] --dry-run: no writes performed")
        return

    processed = 0
    failed = 0
    for row_id, text in needs_embed:
        emb = get_embedding(text)
        if emb is None:
            print(f"[backfill] SKIP (embed failed): {row_id}")
            failed += 1
            continue

        # Fetch current row content to merge embedding into metadata
        fetch = sb.table("organized_memory").select("content").eq("id", row_id).single().execute()
        current_content = fetch.data.get("content") or {}
        if isinstance(current_content, str):
            try:
                current_content = json.loads(current_content)
            except Exception:
                current_content = {}

        meta = current_content.get("metadata") or {}
        meta["embedding"] = emb
        current_content["metadata"] = meta

        sb.table("organized_memory").update({"content": current_content}).eq("id", row_id).execute()
        processed += 1
        print(f"[backfill] embedded {row_id} ({processed}/{len(needs_embed)})")
        time.sleep(0.1)  # stay within Gemini free-tier rate limit

    print(f"[backfill] done — {processed} embedded, {failed} failed, {skip} skipped")


def main():
    parser = argparse.ArgumentParser(description="Backfill Gemini embeddings for pre-T-080 L2 rows.")
    parser.add_argument("--dry-run", action="store_true", help="Report count without writing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
