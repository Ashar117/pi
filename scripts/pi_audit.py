"""scripts/pi_audit.py — CLI for the memory audit system (T-082).

Commands:
    digest                 Run a full audit pass now and write a digest file.
    review                 Print the latest digest to the terminal.
    keep <id>              Remove the flag from a row (mark it OK).
    delete <id> --confirm  Hard-delete a row (irreversible). --confirm required.
    restore <id>           Un-archive a row (status='archived' -> 'active').
    merge <keep_id> <drop_id>  Archive the drop row; keep the other.
    state                  Show current audit state.

Examples:
    python scripts/pi_audit.py digest
    python scripts/pi_audit.py review
    python scripts/pi_audit.py keep 4f2a8b
    python scripts/pi_audit.py delete 4f2a8b --confirm
    python scripts/pi_audit.py merge 4f2a8b 7c3e91

IDs may be passed as full UUIDs or as the first 8+ characters (prefix match).
"""
from __future__ import annotations

import os
import sys
import argparse
import json
from datetime import datetime, timezone

# Make `import memory.*`, `import tools.*` work when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.audit import (
    run_audit, load_audit_state, _audit_state_path,
)


def _make_memory_tools():
    """Construct a MemoryTools from .env. Imported lazily to keep --help fast."""
    from app.config import SUPABASE_URL, SUPABASE_KEY
    from tools.tools_memory import MemoryTools
    return MemoryTools(SUPABASE_URL, SUPABASE_KEY)


def _resolve_id(memory_tools, table: str, prefix: str) -> str | None:
    """Resolve a prefix to a full UUID via Supabase. Returns None on ambiguity / miss."""
    if len(prefix) >= 32:
        return prefix  # already a full UUID
    if len(prefix) < 6:
        print(f"[pi_audit] id prefix too short (need ≥6 chars): {prefix}", file=sys.stderr)
        return None
    try:
        r = (memory_tools.supabase.table(table)
             .select("id").like("id", f"{prefix}%").limit(2).execute())
        rows = r.data or []
    except Exception as e:
        print(f"[pi_audit] lookup error: {e}", file=sys.stderr)
        return None
    if not rows:
        print(f"[pi_audit] no row found for prefix {prefix}", file=sys.stderr)
        return None
    if len(rows) > 1:
        print(f"[pi_audit] ambiguous prefix {prefix} — matches {len(rows)} rows", file=sys.stderr)
        return None
    return rows[0]["id"]


# ── Command handlers ─────────────────────────────────────────────────────────

def cmd_digest(args) -> int:
    """Run a full audit pass and write the digest."""
    from tools.tools_obsidian import render_audit_digest, _default_vault_root
    mt = _make_memory_tools()
    print("[pi_audit] running full audit pass...")
    run = run_audit(mt, dry_run=args.dry_run)
    print(f"  flagged:  {len(run.flagged)}")
    print(f"  archived: {len(run.archived)}")
    print(f"  deleted:  {len(run.deleted)}")
    print(f"  merges:   {len(run.merge_suggestions)}")
    print(f"  errors:   {len(run.errors)}")
    if not args.dry_run:
        res = render_audit_digest(run, _default_vault_root())
        print(f"  digest:   {res['path']}")
    else:
        print("  (dry-run — no state changes, no digest written)")
    return 0 if not run.errors else 1


def cmd_review(args) -> int:
    """Print the most recent digest to the terminal."""
    from tools.tools_obsidian import _default_vault_root
    vault = _default_vault_root()
    digest_dir = os.path.join(vault, "notes", "memory", "audit")
    if not os.path.isdir(digest_dir):
        print("[pi_audit] no digests yet — run `python scripts/pi_audit.py digest` first")
        return 1
    files = sorted(f for f in os.listdir(digest_dir)
                   if f.endswith(".md") and f != "latest.md")
    if not files:
        print("[pi_audit] no digest files in", digest_dir)
        return 1
    latest = os.path.join(digest_dir, files[-1])
    with open(latest, "r", encoding="utf-8") as f:
        print(f.read())
    print(f"\n_(source: {latest})_")
    return 0


def cmd_keep(args) -> int:
    """Remove flagged_at from a row's metadata. Idempotent."""
    mt = _make_memory_tools()
    row_id = _resolve_id(mt, "organized_memory", args.id)
    if not row_id:
        return 1
    try:
        r = mt.supabase.table("organized_memory").select("content").eq("id", row_id).limit(1).execute()
        rows = r.data or []
        if not rows:
            print(f"[pi_audit] row {row_id[:8]} not found", file=sys.stderr)
            return 1
        content = rows[0].get("content") or {}
        if isinstance(content, dict):
            meta = content.get("metadata") or {}
            if isinstance(meta, dict):
                meta.pop("flagged_at", None)
                meta.pop("flag_reason", None)
                meta.pop("flag_rule", None)
                content["metadata"] = meta
                mt.supabase.table("organized_memory").update(
                    {"content": content}
                ).eq("id", row_id).execute()
        print(f"[pi_audit] keep: cleared flags on {row_id[:8]}")
        return 0
    except Exception as e:
        print(f"[pi_audit] keep failed: {e}", file=sys.stderr)
        return 1


def cmd_delete(args) -> int:
    """Hard-delete a row. Requires --confirm."""
    if not args.confirm:
        print("[pi_audit] delete refused — pass --confirm to actually delete", file=sys.stderr)
        return 2
    mt = _make_memory_tools()
    row_id = _resolve_id(mt, "organized_memory", args.id)
    if not row_id:
        return 1
    try:
        mt.supabase.table("organized_memory").delete().eq("id", row_id).execute()
        print(f"[pi_audit] hard-deleted L2 row {row_id[:8]}")
        return 0
    except Exception as e:
        print(f"[pi_audit] delete failed: {e}", file=sys.stderr)
        return 1


def cmd_restore(args) -> int:
    """Un-archive a row: status='archived' -> 'active'."""
    mt = _make_memory_tools()
    row_id = _resolve_id(mt, "organized_memory", args.id)
    if not row_id:
        return 1
    try:
        mt.supabase.table("organized_memory").update(
            {"status": "active"}
        ).eq("id", row_id).execute()
        print(f"[pi_audit] restored L2 row {row_id[:8]} to status=active")
        return 0
    except Exception as e:
        print(f"[pi_audit] restore failed: {e}", file=sys.stderr)
        return 1


def cmd_merge(args) -> int:
    """Archive the drop row; the keep row is untouched."""
    mt = _make_memory_tools()
    keep_id = _resolve_id(mt, "organized_memory", args.keep_id)
    drop_id = _resolve_id(mt, "organized_memory", args.drop_id)
    if not keep_id or not drop_id:
        return 1
    if keep_id == drop_id:
        print("[pi_audit] merge refused — keep_id and drop_id resolved to same row",
              file=sys.stderr)
        return 1
    try:
        mt.supabase.table("organized_memory").update(
            {"status": "archived"}
        ).eq("id", drop_id).execute()
        print(f"[pi_audit] merged: archived {drop_id[:8]}, kept {keep_id[:8]}")
        return 0
    except Exception as e:
        print(f"[pi_audit] merge failed: {e}", file=sys.stderr)
        return 1


def cmd_state(args) -> int:
    """Print current audit state."""
    state = load_audit_state()
    if not state:
        print("[pi_audit] no audit state yet — run `digest` to bootstrap")
        return 0
    print(f"[pi_audit] state file: {_audit_state_path()}")
    print(json.dumps(state, indent=2))
    return 0


# ── Argparse wiring ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pi_audit",
        description="Memory audit CLI for Project Pi (T-082).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pd = sub.add_parser("digest", help="Run full audit + write weekly digest")
    pd.add_argument("--dry-run", action="store_true",
                    help="Detect only, no state changes, no digest written.")
    pd.set_defaults(func=cmd_digest)

    pr = sub.add_parser("review", help="Print the latest digest")
    pr.set_defaults(func=cmd_review)

    pk = sub.add_parser("keep", help="Clear the flag on an L2 row")
    pk.add_argument("id", help="UUID or first 6+ chars of the row id")
    pk.set_defaults(func=cmd_keep)

    pdel = sub.add_parser("delete", help="Hard-delete an L2 row (--confirm required)")
    pdel.add_argument("id")
    pdel.add_argument("--confirm", action="store_true",
                      help="REQUIRED — destructive operation.")
    pdel.set_defaults(func=cmd_delete)

    prs = sub.add_parser("restore", help="Un-archive an L2 row")
    prs.add_argument("id")
    prs.set_defaults(func=cmd_restore)

    pm = sub.add_parser("merge", help="Archive drop row; keep the other")
    pm.add_argument("keep_id")
    pm.add_argument("drop_id")
    pm.set_defaults(func=cmd_merge)

    ps = sub.add_parser("state", help="Print current audit state JSON")
    ps.set_defaults(func=cmd_state)

    return p


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
