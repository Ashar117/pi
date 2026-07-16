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

Operates on data/pi.db.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
_PUBLIC_DB = _ROOT / "data" / "pi.db"


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db_path() -> Path:
    """Return the SQLite path (data/pi.db)."""
    return _PUBLIC_DB


def _connect() -> sqlite3.Connection:
    path = _db_path()
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
    con = _connect()
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
    """Semantic search (dense cosine + BM25 fusion, T-302) → show top candidates
    with scores → confirm → invalidate. Falls back to plain word-overlap
    scoring if MemoryTools/retrieve is unavailable — offline L3 search still
    works without an embedding provider (degrades to BM25 automatically).
    """
    query = args.query
    if not _PUBLIC_DB.exists():
        print(f"[memory_cli] DB not found: {_PUBLIC_DB}", file=sys.stderr)
        sys.exit(1)

    candidates: list[tuple] = []  # (id, content, importance, category, score)
    try:
        from tools.tools_memory import MemoryTools
        mt = MemoryTools(supabase_url="", supabase_key="", sqlite_path=str(_PUBLIC_DB))
        candidates = [
            (h["id"], h["content"], h["importance"], h["category"], h.get("score", 0.0))
            for h in mt.retrieve(query, k=3, tiers=("l3",))
        ]
    except Exception:
        candidates = []

    if not candidates:
        # Fallback: plain word-overlap scoring (T-139/T-145 era behavior).
        con = _connect()
        all_rows = con.execute(
            "SELECT id, content, importance, category, invalid_at "
            "FROM l3_cache WHERE invalid_at IS NULL "
            "ORDER BY importance DESC, created_at DESC"
        ).fetchall()
        con.close()
        q_words = set(query.lower().split())
        scored = []
        for rid, content, imp, cat, _ in all_rows:
            c_words = set((content or "").lower().split())
            score = len(q_words & c_words) / max(len(q_words), 1)
            if score > 0:
                scored.append((score, (rid, content, imp, cat)))
        scored.sort(key=lambda x: x[0], reverse=True)
        candidates = [(rid, content, imp, cat, score) for score, (rid, content, imp, cat) in scored[:3]]

    if not candidates:
        print(f"[memory_cli] No rows matching '{args.query}'.")
        return

    print(f"[memory_cli] Top matches for '{args.query}':")
    for i, (rid, content, imp, cat, score) in enumerate(candidates):
        print(f"  [{i+1}] {rid[:8]}  imp={imp}  score={score:.2f}  [{cat}]  {(content or '')[:100]!r}")

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
    con = _connect()
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
    con = _connect()
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
    con = _connect()
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


def _parse_iso(s: str | None):
    """Parse an ISO timestamp that may use 'Z' or '+00:00' suffix. None on failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def cmd_forgotten(args: argparse.Namespace) -> None:
    """T-301: the forgetting ledger — recently forgotten rows, classified by why.

    Precedence (a row is classified exactly once, never shown twice):
      1. CONTRADICTED — invalid_at is set (a newer fact superseded it)
      2. EXPIRED      — active_until has passed (scheduled/inferred expiry, or
                         decay-archive) and invalid_at is NOT set
      3. MERGED       — superseded_by is set (semantic-dedup merge loser) and
                         neither of the above applies

    SQL stays dumb (pulls every row carrying any of the three signals);
    classification and the --days window are applied in Python since the
    three timestamp columns aren't uniformly formatted across write paths.

    Caveat: dedup-merge (T-125b) does not record a merge timestamp, so MERGED
    rows are always included regardless of --days — there is nothing to window on.
    """
    con = _connect()
    days = getattr(args, "days", None)
    if days is None:
        days = 7
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    rows = con.execute(
        "SELECT id, content, importance, category, active_until, invalid_at, superseded_by "
        "FROM l3_cache WHERE invalid_at IS NOT NULL "
        "   OR active_until IS NOT NULL "
        "   OR (superseded_by IS NOT NULL AND superseded_by != '')"
    ).fetchall()

    classified = []
    for rid, content, imp, cat, active_until, invalid_at, superseded_by in rows:
        invalid_dt = _parse_iso(invalid_at)
        active_dt = _parse_iso(active_until)

        if invalid_dt is not None:
            reason, when_dt, pointer_id = "CONTRADICTED", invalid_dt, None
        elif active_dt is not None and active_dt < now:
            reason, when_dt, pointer_id = "EXPIRED", active_dt, None
        elif superseded_by:
            reason, when_dt, pointer_id = "MERGED", None, superseded_by
        else:
            continue

        if when_dt is not None and when_dt < cutoff:
            continue

        classified.append({
            "id": rid, "content": content, "importance": imp, "category": cat,
            "reason": reason, "when": when_dt.isoformat() if when_dt else None,
            "pointer_id": pointer_id,
        })

    # Resolve MERGED pointer content snippets in one batch query.
    pointer_ids = [c["pointer_id"] for c in classified if c["pointer_id"]]
    winners = {}
    if pointer_ids:
        ph = ",".join("?" * len(pointer_ids))
        winners = dict(con.execute(
            f"SELECT id, content FROM l3_cache WHERE id IN ({ph})", pointer_ids
        ).fetchall())
    con.close()

    for c in classified:
        if c["pointer_id"]:
            c["superseded_by_snippet"] = (winners.get(c["pointer_id"]) or "")[:60]

    with_time = sorted((c for c in classified if c["when"]), key=lambda c: c["when"], reverse=True)
    without_time = [c for c in classified if not c["when"]]
    classified = with_time + without_time

    if args.json:
        print(json.dumps(classified, indent=2, ensure_ascii=False))
        return

    if not classified:
        print(f"[memory_cli] Nothing forgotten in the last {days} day(s).")
        return

    counts = {"EXPIRED": 0, "CONTRADICTED": 0, "MERGED": 0}
    print(f"[memory_cli] Forgotten in the last {days} day(s):")
    for c in classified:
        counts[c["reason"]] += 1
        when_str = (c["when"] or "—")[:19]
        line = f"  {c['id'][:8]}  {c['reason']:12}  {when_str}  {(c['content'] or '')[:60]!r}"
        if c["reason"] == "MERGED":
            line += f"  -> merged into: {c.get('superseded_by_snippet', '')!r}"
        print(line)
    print(
        f"[memory_cli] {counts['EXPIRED']} expired, "
        f"{counts['CONTRADICTED']} contradicted, {counts['MERGED']} merged"
    )


# ── arg parser ─────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memory_cli",
        description="Inspect and steer Pi's L3 memory.",
    )

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

    # forgotten
    fgn = sub.add_parser("forgotten", help="The forgetting ledger — what/when/why")
    fgn.add_argument("--days", type=int, default=7)
    fgn.add_argument("--json", action="store_true")

    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    dispatch = {
        "list": cmd_list,
        "forget": cmd_forget,
        "pin": cmd_pin,
        "why": cmd_why,
        "forgotten": cmd_forgotten,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
