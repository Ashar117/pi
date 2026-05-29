#!/usr/bin/env python3
"""scripts/memory_cli.py — T-133: inspect and steer Pi's L3 memory from the CLI.

Subcommands:
  list    print L3 rows, optionally filtered by tier/category
  forget  semantic search → confirm → invalidate (soft-delete)
  pin     set importance=10 on a row; no-op if pinned column missing (pre-T-135)
  why     show full metadata trace for a row

Usage:
  python scripts/memory_cli.py list [--tier l3] [--category X] [--limit 20]
  python scripts/memory_cli.py forget "search query"
  python scripts/memory_cli.py pin <id-prefix>
  python scripts/memory_cli.py why <id-prefix>

Privacy:
  Default: operates on data/pi.db only.
  --god flag requires ALSO setting PI_GOD_CLI=1 env. Either alone refuses.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
_PUBLIC_DB = _ROOT / "data" / "pi.db"
_GOD_DB = _ROOT / "data" / "god_memory.db"
_GOD_ENV = "PI_GOD_CLI"


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db_path(god: bool) -> Path:
    """Return SQLite path. god=True requires PI_GOD_CLI=1 in env."""
    if god:
        if os.environ.get(_GOD_ENV, "").lower() not in ("1", "on", "true", "yes"):
            print(
                f"[memory_cli] Refused: --god requires env {_GOD_ENV}=1. "
                "See PI.md §1 rule 7 — god memory is private by design.",
                file=sys.stderr,
            )
            sys.exit(1)
        return _GOD_DB
    return _PUBLIC_DB


def _connect(god: bool) -> sqlite3.Connection:
    path = _db_path(god)
    if not path.exists():
        print(f"[memory_cli] DB not found: {path}", file=sys.stderr)
        sys.exit(1)
    return sqlite3.connect(str(path))


def _row_cols(con: sqlite3.Connection) -> list[str]:
    """Return all column names of l3_cache for this DB file."""
    cur = con.execute("PRAGMA table_info(l3_cache)")
    return [row[1] for row in cur.fetchall()]


def _find_by_prefix(con: sqlite3.Connection, prefix: str) -> tuple | None:
    """Return (id, content, importance, category, ...) matching id prefix, or None."""
    cur = con.execute(
        "SELECT * FROM l3_cache WHERE id LIKE ? LIMIT 2",
        [f"{prefix}%"],
    )
    rows = cur.fetchall()
    if len(rows) == 0:
        return None
    if len(rows) > 1:
        print(f"[memory_cli] Ambiguous prefix '{prefix}' matches {len(rows)} rows. Use more chars.",
              file=sys.stderr)
        sys.exit(1)
    return rows[0]


# ── formatters ─────────────────────────────────────────────────────────────────

def _short_id(full_id: str, full: bool = False) -> str:
    return full_id if full else full_id[:8]


def _fmt_row(cols: list[str], row: tuple, full_id: bool = False) -> str:
    d = dict(zip(cols, row))
    sid = _short_id(d.get("id", ""), full_id)
    content = (d.get("content") or "")[:120]
    imp = d.get("importance", "?")
    cat = d.get("category", "?")
    invalid = " [INVALIDATED]" if d.get("invalid_at") else ""
    return f"  {sid}  imp={imp}  [{cat}]{invalid}  {content!r}"


# ── subcommands ────────────────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> None:
    """Print L3 rows, filtered by tier/category/limit."""
    con = _connect(args.god)
    cols = _row_cols(con)

    where_clauses = []
    params: list = []

    if not args.include_archived:
        where_clauses.append("invalid_at IS NULL")

    if args.category:
        where_clauses.append("category = ?")
        params.append(args.category)

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    limit = args.limit or 20
    params.append(limit)

    cur = con.execute(
        f"SELECT * FROM l3_cache {where} ORDER BY importance DESC, created_at DESC LIMIT ?",
        params,
    )
    rows = cur.fetchall()
    con.close()

    if args.json:
        print(json.dumps([dict(zip(cols, r)) for r in rows], indent=2, ensure_ascii=False))
        return

    print(f"[memory_cli] L3 — {len(rows)} rows (limit={limit})")
    for r in rows:
        print(_fmt_row(cols, r, full_id=args.full_id))


def cmd_forget(args: argparse.Namespace) -> None:
    """Semantic search → show top candidates → confirm → invalidate."""
    con = _connect(args.god)
    query = args.query.lower()
    cur = con.execute(
        "SELECT id, content, importance, category, invalid_at "
        "FROM l3_cache WHERE invalid_at IS NULL "
        "ORDER BY importance DESC, created_at DESC"
    )
    all_rows = cur.fetchall()
    con.close()

    # Score by simple word overlap
    q_words = set(query.split())
    scored = []
    for row in all_rows:
        rid, content, imp, cat, _ = row
        c_words = set((content or "").lower().split())
        score = len(q_words & c_words) / max(len(q_words), 1)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = [r for _, r in scored[:3]]

    if not candidates:
        print(f"[memory_cli] No rows matching '{args.query}'.")
        return

    print(f"[memory_cli] Top matches for '{args.query}':")
    for i, (rid, content, imp, cat, _) in enumerate(candidates):
        print(f"  [{i+1}] {rid[:8]}  imp={imp}  [{cat}]  {(content or '')[:100]!r}")

    if args.yes:
        chosen = candidates
    else:
        raw = input("\nForget which? Enter number(s) comma-separated, or 'n' to cancel: ").strip()
        if raw.lower() == "n" or not raw:
            print("Cancelled.")
            return
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(",")]
            chosen = [candidates[i] for i in indices if 0 <= i < len(candidates)]
        except (ValueError, IndexError):
            print("[memory_cli] Invalid selection. Cancelled.")
            return

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    con = _connect(args.god)
    for row in chosen:
        rid = row[0]
        con.execute(
            "UPDATE l3_cache SET invalid_at = ? WHERE id = ? AND invalid_at IS NULL",
            [now, rid],
        )
        print(f"[memory_cli] Invalidated {rid[:8]}")
    con.commit()
    con.close()


def cmd_pin(args: argparse.Namespace) -> None:
    """Set importance=10 on a row. No-op if pinned column missing (pre-T-135)."""
    con = _connect(args.god)
    cols = _row_cols(con)
    row = _find_by_prefix(con, args.id_prefix)
    if row is None:
        print(f"[memory_cli] No row with id prefix '{args.id_prefix}'.", file=sys.stderr)
        con.close()
        sys.exit(1)

    d = dict(zip(cols, row))
    rid = d["id"]

    if "pinned" in cols:
        con.execute(
            "UPDATE l3_cache SET importance = 10, pinned = 1 WHERE id = ?",
            [rid],
        )
        print(f"[memory_cli] Pinned {rid[:8]} (importance=10, pinned=1)")
    else:
        con.execute("UPDATE l3_cache SET importance = 10 WHERE id = ?", [rid])
        print(f"[memory_cli] Pinned {rid[:8]} (importance=10; pinned column not yet present — see T-135)")

    con.commit()
    con.close()


def cmd_why(args: argparse.Namespace) -> None:
    """Print full metadata trace for a row."""
    con = _connect(args.god)
    cols = _row_cols(con)
    row = _find_by_prefix(con, args.id_prefix)
    con.close()

    if row is None:
        print(f"[memory_cli] No row with id prefix '{args.id_prefix}'.", file=sys.stderr)
        sys.exit(1)

    d = dict(zip(cols, row))

    if args.json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return

    print(f"[memory_cli] Provenance trace for {d.get('id', '?')[:8]}")
    print(f"  Full ID     : {d.get('id', '?')}")
    print(f"  Content     : {(d.get('content') or '')[:200]!r}")
    print(f"  Importance  : {d.get('importance', '?')}")
    print(f"  Category    : {d.get('category', '?')}")
    print(f"  Created     : {d.get('created_at', '?')}")
    print(f"  Active until: {d.get('active_until') or 'permanent'}")
    print(f"  Invalid at  : {d.get('invalid_at') or 'active'}")
    print(f"  Kind        : {d.get('kind') or 'stated'}")
    print(f"  Source ID   : {d.get('source_id') or '-'}")
    print(f"  Superseded  : {d.get('superseded_by') or '-'}")
    if "formula" in d:
        print(f"  Formula     : {d.get('formula') or '-'}")


# ── arg parser ─────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memory_cli",
        description="Inspect and steer Pi's L3 memory.",
    )
    p.add_argument("--god", action="store_true",
                   help=f"Target god_memory.db (also requires {_GOD_ENV}=1 env)")

    sub = p.add_subparsers(dest="cmd", required=True)

    # list
    ls = sub.add_parser("list", help="Print L3 rows")
    ls.add_argument("--tier", choices=["l3"], default="l3")
    ls.add_argument("--category", "-c", default="")
    ls.add_argument("--limit", "-n", type=int, default=20)
    ls.add_argument("--include-archived", action="store_true")
    ls.add_argument("--full-id", action="store_true")
    ls.add_argument("--json", action="store_true")

    # forget
    fg = sub.add_parser("forget", help="Search → confirm → invalidate rows")
    fg.add_argument("query", help="Search query to find rows to forget")
    fg.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    # pin
    pn = sub.add_parser("pin", help="Set importance=10 on a row")
    pn.add_argument("id_prefix", metavar="ID-PREFIX", help="Row ID prefix (min 4 chars)")

    # why
    wy = sub.add_parser("why", help="Show full metadata trace")
    wy.add_argument("id_prefix", metavar="ID-PREFIX", help="Row ID prefix (min 4 chars)")
    wy.add_argument("--json", action="store_true")

    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    dispatch = {
        "list": cmd_list,
        "forget": cmd_forget,
        "pin": cmd_pin,
        "why": cmd_why,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
