"""agent/caretaker.py — T-125a: derived-fact auto-recompute.

Pi's L3 is append-only. Without maintenance, derived facts ("User is 19") go
stale; stated facts ("User born 2006-08-17") never feed forward. The caretaker
is a continuous-process replacement for one-off hygiene scripts (T-078
invalidation, T-080 dedup).

Stage A (T-125a, this module): derived-fact recompute only.
Stage B (T-125b, future): embedding dedup as caretaker.full()
Stage C (T-125c, future): contradiction resolution + deep Haiku review

Schema (lite mode):
    L3 row may carry {kind: 'derived', source_id: <orig_id>, formula: <name>,
    recompute_after: <iso>}. When kind='derived' and recompute_after < now,
    the caretaker re-runs the formula against the source row and updates
    content + recompute_after.

Triggers:
  - per-bubble close (cheap; idempotent)
  - session-exit step
  - daily retention_tick.py cron

Concurrency: data/caretaker.lock (filelock) prevents bubble + cron from racing.
"""
from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from filelock import FileLock, Timeout as _LockTimeout
    _FILELOCK_OK = True
except ImportError:
    _FILELOCK_OK = False
    FileLock = None
    _LockTimeout = Exception

_ROOT = Path(__file__).parent.parent
_LOCK_PATH = _ROOT / "data" / "caretaker.lock"


# ── Formula registry ──────────────────────────────────────────────────────────
# Each formula: (source_content: str, now: datetime) -> (new_content: str, next_recompute: datetime)

def _formula_age_from_birthday(source: str, now: datetime) -> Tuple[str, datetime]:
    """Source contains 'born YYYY-MM-DD' (or 'birthday YYYY-MM-DD').
    Output: 'User is <N> years old (computed YYYY-MM-DD)'.
    Recompute after next birthday.
    """
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", source)
    if not m:
        raise ValueError(f"could not extract birthday date from: {source!r}")
    by, bm, bd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    bday_this_year = datetime(now.year, bm, bd, tzinfo=timezone.utc)
    if bday_this_year > now:
        age = now.year - by - 1
        next_recompute = bday_this_year
    else:
        age = now.year - by
        try:
            next_recompute = datetime(now.year + 1, bm, bd, tzinfo=timezone.utc)
        except ValueError:
            # Feb 29 — use Mar 1 in non-leap years
            next_recompute = datetime(now.year + 1, 3, 1, tzinfo=timezone.utc)
    content = f"User is {age} years old (computed {now.strftime('%Y-%m-%d')})"
    return content, next_recompute


def _formula_days_until_date(source: str, now: datetime) -> Tuple[str, datetime]:
    """Source contains a target date YYYY-MM-DD. Output: 'N days until <date>'.
    Recompute daily until the date passes.
    """
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", source)
    if not m:
        raise ValueError(f"could not extract target date from: {source!r}")
    target = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    days = (target.date() - now.date()).days
    if days >= 0:
        content = f"{days} days until {target.strftime('%Y-%m-%d')}"
        next_recompute = now + timedelta(days=1)
    else:
        content = f"{abs(days)} days since {target.strftime('%Y-%m-%d')}"
        # No need to recompute after the date passed for years
        next_recompute = now + timedelta(days=30)
    return content, next_recompute


def _formula_days_since_date(source: str, now: datetime) -> Tuple[str, datetime]:
    """Source contains a past date YYYY-MM-DD. Output: 'N days since <date>'."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", source)
    if not m:
        raise ValueError(f"could not extract date from: {source!r}")
    target = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    days = (now.date() - target.date()).days
    content = f"{days} days since {target.strftime('%Y-%m-%d')}"
    next_recompute = now + timedelta(days=1)
    return content, next_recompute


_FORMULAS: Dict[str, Callable[[str, datetime], Tuple[str, datetime]]] = {
    "age_from_birthday": _formula_age_from_birthday,
    "days_until_date": _formula_days_until_date,
    "days_since_date": _formula_days_since_date,
}


# ── Detection — find derivable facts in newly-written content ────────────────

def detect_derivable(content: str) -> Optional[Tuple[str, datetime]]:
    """Inspect a freshly-written fact. If it looks derivable (e.g. contains a
    birthday), return (formula_name, initial_recompute_after); otherwise None.

    Currently detects:
      - 'born YYYY-MM-DD' or 'birthday YYYY-MM-DD' → age_from_birthday
    """
    low = content.lower()
    if "born" in low or "birthday" in low:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", content)
        if m:
            # Initial recompute: now (so caretaker fires on next bubble close)
            return ("age_from_birthday", datetime.now(timezone.utc))
    return None


# ── Caretaker core ────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _track(event: str, exc: Optional[Exception] = None, **context) -> None:
    try:
        from agent.observability import track_silent
        track_silent(f"caretaker.{event}", exc, context=context)
    except Exception:
        pass


def _select_due(conn: sqlite3.Connection, now_iso: str) -> List[Tuple]:
    """Return (id, content, source_id, formula, recompute_after) rows that are due."""
    cur = conn.execute(
        """
        SELECT id, content, source_id, formula, recompute_after
        FROM l3_cache
        WHERE kind = 'derived'
          AND invalid_at IS NULL
          AND (recompute_after IS NULL OR recompute_after <= ?)
        """,
        (now_iso,),
    )
    return cur.fetchall()


def _read_source(conn: sqlite3.Connection, source_id: str) -> Optional[str]:
    cur = conn.execute("SELECT content FROM l3_cache WHERE id = ?", (source_id,))
    row = cur.fetchone()
    return row[0] if row else None


def lite(db_path: Path, dry_run: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Run lite-mode caretaker against the given SQLite DB.

    Returns {recomputed: int, skipped: int, errors: int, applied: bool, dry_run: bool}.
    Never raises — all per-row failures are caught and recorded via track_silent.
    """
    now = now or _now()
    now_iso = now.isoformat()
    stats = {"recomputed": 0, "skipped": 0, "errors": 0, "applied": False, "dry_run": dry_run}

    lock_ctx = None
    if _FILELOCK_OK:
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_ctx = FileLock(str(_LOCK_PATH), timeout=10)
            lock_ctx.acquire()
        except _LockTimeout:
            _track("lock_timeout", None, db_path=str(db_path))
            return stats

    try:
        if not Path(db_path).exists():
            return stats
        conn = sqlite3.connect(str(db_path))
        try:
            due = _select_due(conn, now_iso)
            for row in due:
                rid, _old_content, source_id, formula_name, _recompute_after = row
                formula_fn = _FORMULAS.get(formula_name)
                if formula_fn is None:
                    _track("unknown_formula", None, id=rid, formula=formula_name)
                    stats["errors"] += 1
                    continue
                if not source_id:
                    _track("missing_source_id", None, id=rid)
                    stats["errors"] += 1
                    continue
                source_content = _read_source(conn, source_id)
                if source_content is None:
                    _track("source_missing", None, id=rid, source_id=source_id)
                    stats["errors"] += 1
                    continue
                try:
                    new_content, next_recompute = formula_fn(source_content, now)
                except Exception as e:
                    _track("formula_failed", e, id=rid, formula=formula_name)
                    stats["errors"] += 1
                    continue

                if dry_run:
                    stats["skipped"] += 1
                    continue

                conn.execute(
                    "UPDATE l3_cache SET content = ?, recompute_after = ? WHERE id = ?",
                    (new_content, next_recompute.isoformat(), rid),
                )
                stats["recomputed"] += 1
            if not dry_run:
                conn.commit()
        finally:
            conn.close()
    finally:
        if lock_ctx is not None:
            try:
                lock_ctx.release()
            except Exception:
                pass

    stats["applied"] = stats["recomputed"] > 0
    return stats


def backfill(db_path: Path, dry_run: bool = False) -> Dict[str, Any]:
    """One-time scan of existing L3 for derivable facts (born YYYY-MM-DD) that
    do NOT yet have a paired derived row. Creates the derived row and queues
    it for immediate recompute by the caretaker.
    """
    import uuid
    stats = {"derived_spawned": 0, "examined": 0, "dry_run": dry_run}
    if not Path(db_path).exists():
        return stats
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT id, content FROM l3_cache "
            "WHERE invalid_at IS NULL AND (kind IS NULL OR kind = '')"
        )
        sources = cur.fetchall()
        existing_sources = set()
        cur2 = conn.execute(
            "SELECT source_id FROM l3_cache WHERE kind = 'derived' AND source_id IS NOT NULL"
        )
        for (sid,) in cur2.fetchall():
            existing_sources.add(sid)

        for src_id, content in sources:
            stats["examined"] += 1
            detected = detect_derivable(content or "")
            if detected is None:
                continue
            if src_id in existing_sources:
                continue
            formula_name, recompute_after = detected
            if dry_run:
                stats["derived_spawned"] += 1
                continue
            derived_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO l3_cache (id, content, importance, category, "
                "active_until, created_at, kind, source_id, recompute_after, formula) "
                "VALUES (?, ?, ?, ?, ?, ?, 'derived', ?, ?, ?)",
                (
                    derived_id,
                    f"(pending recompute from {src_id})",
                    8,  # high importance — caretaker recomputes immediately
                    "derived",
                    None,
                    datetime.now(timezone.utc).isoformat(),
                    src_id,
                    recompute_after.isoformat(),
                    formula_name,
                ),
            )
            stats["derived_spawned"] += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return stats


# ── T-125b — full mode: embedding-based dedup ─────────────────────────────────

_DEDUP_COSINE_THRESHOLD = 0.92
_DEDUP_MAX_SCAN = 500  # per run; resume token tracks last_processed_id


def _get_embedding_safe(text: str) -> Optional[List[float]]:
    """Wrap memory.semantic_dedup.get_embedding with a failure shield."""
    try:
        from memory.semantic_dedup import get_embedding
        return get_embedding(text)
    except Exception:
        return None


def _cosine_safe(a: List[float], b: List[float]) -> float:
    try:
        from memory.semantic_dedup import cosine_similarity
        return cosine_similarity(a, b)
    except Exception:
        return 0.0


def _pick_winner(row_a: Tuple, row_b: Tuple) -> Tuple[Tuple, Tuple]:
    """Given two L3 rows (id, content, importance, created_at), return (winner, loser).

    Policy:
      - Higher importance wins.
      - If tied, newer created_at wins.
      - If still tied, lexicographically-smaller id wins (deterministic).
    """
    _, _, imp_a, ca_a = row_a
    _, _, imp_b, ca_b = row_b
    imp_a = imp_a or 0
    imp_b = imp_b or 0
    if imp_a > imp_b:
        return row_a, row_b
    if imp_b > imp_a:
        return row_b, row_a
    # Tied importance — newer wins
    if (ca_a or "") > (ca_b or ""):
        return row_a, row_b
    if (ca_b or "") > (ca_a or ""):
        return row_b, row_a
    # Tied created_at — id ordering for determinism
    return (row_a, row_b) if row_a[0] <= row_b[0] else (row_b, row_a)


def full(
    db_path: Path,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    max_scan: int = _DEDUP_MAX_SCAN,
    cosine_threshold: float = _DEDUP_COSINE_THRESHOLD,
) -> Dict[str, Any]:
    """T-125b — full caretaker pass: lite() + embedding-based dedup.

    Walks active L3 rows (excluding derived/invalidated/already-superseded);
    for each row, finds neighbours in the SAME category with cosine >=
    cosine_threshold; marks loser with superseded_by = winner_id.

    Returns combined stats:
      {recomputed, skipped, errors,        ← from lite()
       deduped, dedup_skipped, dedup_errors, applied, dry_run}

    Bounded: scans at most max_scan rows per run. Skips entries without
    a computable embedding. Never raises.
    """
    now = now or _now()
    stats: Dict[str, Any] = {
        "recomputed": 0, "skipped": 0, "errors": 0,
        "deduped": 0, "dedup_skipped": 0, "dedup_errors": 0,
        "applied": False, "dry_run": dry_run,
    }

    # Stage 1: run lite() first (derived recompute)
    lite_stats = lite(db_path, dry_run=dry_run, now=now)
    stats["recomputed"] = lite_stats["recomputed"]
    stats["skipped"] = lite_stats["skipped"]
    stats["errors"] = lite_stats["errors"]

    # Stage 2: dedup pass
    lock_ctx = None
    if _FILELOCK_OK:
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_ctx = FileLock(str(_LOCK_PATH), timeout=10)
            lock_ctx.acquire()
        except _LockTimeout:
            _track("dedup_lock_timeout", None, db_path=str(db_path))
            return stats

    try:
        if not Path(db_path).exists():
            return stats
        conn = sqlite3.connect(str(db_path))
        try:
            # Fetch candidates: stated facts only (no derived placeholders),
            # not invalidated, not already superseded.
            cur = conn.execute(
                """
                SELECT id, content, importance, created_at, category
                FROM l3_cache
                WHERE invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                  AND (kind IS NULL OR kind != 'derived')
                ORDER BY category, created_at DESC
                LIMIT ?
                """,
                (max_scan,),
            )
            rows = cur.fetchall()

            # Group by category for bounded O(N²)
            by_category: Dict[str, List[Tuple]] = {}
            for r in rows:
                cat = r[4] or ""
                by_category.setdefault(cat, []).append(r[:4])  # drop category col

            # Build embedding cache per category (one pass per row)
            for category, cat_rows in by_category.items():
                embeddings: Dict[str, List[float]] = {}
                for r in cat_rows:
                    rid, content, _, _ = r
                    emb = _get_embedding_safe(content or "")
                    if emb is not None:
                        embeddings[rid] = emb

                # Identify pairs above threshold; resolve to winner/loser
                already_processed: set = set()
                for i, r1 in enumerate(cat_rows):
                    rid_a = r1[0]
                    if rid_a in already_processed:
                        continue
                    emb_a = embeddings.get(rid_a)
                    if emb_a is None:
                        stats["dedup_skipped"] += 1
                        continue
                    for r2 in cat_rows[i + 1:]:
                        rid_b = r2[0]
                        if rid_b in already_processed:
                            continue
                        emb_b = embeddings.get(rid_b)
                        if emb_b is None:
                            continue
                        score = _cosine_safe(emb_a, emb_b)
                        if score < cosine_threshold:
                            continue
                        # Merge — winner stays, loser gets superseded_by
                        winner, loser = _pick_winner(r1, r2)
                        if dry_run:
                            stats["dedup_skipped"] += 1
                        else:
                            try:
                                conn.execute(
                                    "UPDATE l3_cache SET superseded_by = ? WHERE id = ?",
                                    (winner[0], loser[0]),
                                )
                                stats["deduped"] += 1
                            except Exception as e:
                                _track("dedup_update_failed", e, loser_id=loser[0])
                                stats["dedup_errors"] += 1
                        already_processed.add(loser[0])
            if not dry_run:
                conn.commit()
        finally:
            conn.close()
    finally:
        if lock_ctx is not None:
            try:
                lock_ctx.release()
            except Exception:
                pass

    # T-125c: contradiction scan runs alongside dedup in full mode
    try:
        contra_stats = scan_contradictions(db_path, dry_run=dry_run, now=now)
        stats["contradictions_invalidated"] = contra_stats["invalidated"]
        stats["contradictions_found"] = contra_stats["conflicts_found"]
    except Exception as e:
        _track("contradiction_scan_failed", e)
        stats["contradictions_invalidated"] = 0
        stats["contradictions_found"] = 0

    stats["applied"] = (
        stats["recomputed"]
        + stats["deduped"]
        + stats.get("contradictions_invalidated", 0)
    ) > 0
    return stats


# ── T-125c — contradiction scan ───────────────────────────────────────────────

# Words that carry no topical information; stripped when computing topic keys.
_TOPIC_STOPWORDS = frozenset(
    "user the a an is are was were be been being has have had do does did "
    "will would could should may might can would i my me you your they them "
    "his her its our their this that these those of for in on at to with "
    "from by about and or but not no yes very really just only also still "
    "lives live living lived likes liked loves loved hates hated "
    "borns born birth birthday".split()
)


def _topic_key(content: str, n_tokens: int = 2) -> str:
    """Reduce content to a coarse 'topic key' for contradiction grouping.

    Heuristic: lowercase, drop punctuation, drop stopwords, take first N tokens.
    'User lives in Atlanta' and 'User lives in Multan' both reduce to 'atlanta' /
    'multan' under N=1; with N=2 they include the next word.

    For contradiction detection we actually want the TOPIC, not the value. So we
    use the FIRST n_tokens of stopword-stripped content WITHOUT the value tail:
    'User lives in Atlanta' → ['user', 'lives', 'in', 'atlanta'] → strip stops
    → ['atlanta']. That's the wrong direction. Better: keep verbs we stripped.

    Pragmatic choice: extract topic as 'lives_in', 'works_at', etc. by looking
    for known relation verbs. Falls back to first 2 non-stopword tokens.
    """
    import re as _re
    words = _re.findall(r"[a-zA-Z']+", content.lower())
    # Detect known relation patterns first
    joined = " ".join(words)
    for verb_phrase in ("lives in", "works at", "studies at", "based in",
                        "born in", "married to", "located in", "going to"):
        if verb_phrase in joined:
            return verb_phrase.replace(" ", "_")
    # Fallback: first 2 non-stopwords
    content_tokens = [w for w in words if w not in _TOPIC_STOPWORDS]
    return "_".join(content_tokens[:n_tokens])


def _value_tail(content: str) -> str:
    """Extract the 'value' part of a relation statement — the word(s) after the
    known verb phrase. Used to detect actual conflict (Atlanta vs Multan)
    rather than topical similarity (both about location).
    """
    import re as _re
    low = content.lower()
    for verb_phrase in ("lives in", "works at", "studies at", "based in",
                        "born in", "married to", "located in", "going to"):
        idx = low.find(verb_phrase)
        if idx >= 0:
            tail = low[idx + len(verb_phrase):].strip()
            m = _re.match(r"([a-zA-Z'\- ]+)", tail)
            if m:
                return m.group(1).strip()
    return ""


def scan_contradictions(
    db_path: Path,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Walk active L3 rows; for each topic key with multiple distinct values
    in the same category, mark older rows with invalid_at set.

    Skips derived/invalidated/already-superseded rows. Soft-only — sets
    invalid_at; never deletes.

    Returns {invalidated, examined, conflicts_found, errors, dry_run}.
    """
    now = now or _now()
    stats = {"invalidated": 0, "examined": 0, "conflicts_found": 0, "errors": 0, "dry_run": dry_run}
    if not Path(db_path).exists():
        return stats

    lock_ctx = None
    if _FILELOCK_OK:
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_ctx = FileLock(str(_LOCK_PATH), timeout=10)
            lock_ctx.acquire()
        except _LockTimeout:
            _track("contradiction_lock_timeout", None, db_path=str(db_path))
            return stats

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute(
                """
                SELECT id, content, category, created_at
                FROM l3_cache
                WHERE invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                  AND (kind IS NULL OR kind != 'derived')
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()

            # Group by (category, topic_key)
            groups: Dict[Tuple[str, str], List[Tuple]] = {}
            for r in rows:
                stats["examined"] += 1
                rid, content, category, created_at = r
                topic = _topic_key(content or "")
                if not topic:
                    continue
                key = (category or "", topic)
                groups.setdefault(key, []).append(r)

            now_iso = now.isoformat()
            for (cat, topic), group in groups.items():
                if len(group) < 2:
                    continue
                # Distinct values?
                values = {_value_tail(r[1]) for r in group if _value_tail(r[1])}
                if len(values) < 2:
                    continue  # same value across rows — not a contradiction
                stats["conflicts_found"] += 1
                # Newest row already first (ORDER BY DESC). Older rows lose.
                losers = group[1:]
                for loser in losers:
                    if dry_run:
                        continue
                    try:
                        conn.execute(
                            "UPDATE l3_cache SET invalid_at = ? WHERE id = ?",
                            (now_iso, loser[0]),
                        )
                        stats["invalidated"] += 1
                    except Exception as e:
                        _track("contradiction_update_failed", e, loser_id=loser[0])
                        stats["errors"] += 1
            if not dry_run:
                conn.commit()
        finally:
            conn.close()
    finally:
        if lock_ctx is not None:
            try:
                lock_ctx.release()
            except Exception:
                pass
    return stats


# ── T-125c — deep mode (nightly Haiku review) ─────────────────────────────────

def _try_haiku_review(category: str, facts: List[str]) -> Optional[str]:
    """Ask Haiku to surface internal contradictions or stale items in a list
    of category facts. Returns the model's reply or None on failure.
    """
    if not facts:
        return None
    try:
        import os as _os
        api_key = _os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            f"You are reviewing recent facts stored in category '{category}'. "
            "List any pairs that appear to contradict each other, or facts that "
            "look stale and should be re-confirmed with the user. Reply in 3-5 "
            "bullet points or 'no issues' if everything is consistent.\n\n"
            "Facts:\n" + "\n".join(f"- {f}" for f in facts[:40])
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip() if resp.content else None
    except Exception as e:
        _track("deep_haiku_failed", e, category=category)
        return None


def deep(
    db_path: Path,
    dry_run: bool = False,
    categories: Optional[List[str]] = None,
    max_facts_per_category: int = 30,
) -> Dict[str, Any]:
    """Nightly deep review — for each major category, ask Haiku to flag
    contradictions/staleness the heuristic might have missed.

    Output is informational only (logged via track_silent); no mutations.
    """
    stats = {"categories_reviewed": 0, "reviews": [], "dry_run": dry_run}
    if not Path(db_path).exists():
        return stats
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            if categories is None:
                cur = conn.execute(
                    "SELECT DISTINCT category FROM l3_cache "
                    "WHERE invalid_at IS NULL "
                    "  AND (superseded_by IS NULL OR superseded_by = '') "
                    "  AND category IS NOT NULL AND category != ''"
                )
                categories = [r[0] for r in cur.fetchall()]
            for category in categories:
                cur = conn.execute(
                    """
                    SELECT content FROM l3_cache
                    WHERE category = ?
                      AND invalid_at IS NULL
                      AND (superseded_by IS NULL OR superseded_by = '')
                      AND (kind IS NULL OR kind != 'derived')
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (category, max_facts_per_category),
                )
                facts = [r[0] for r in cur.fetchall()]
                if not facts:
                    continue
                stats["categories_reviewed"] += 1
                if dry_run:
                    stats["reviews"].append({"category": category, "review": "(dry_run skipped)"})
                    continue
                review = _try_haiku_review(category, facts)
                if review:
                    stats["reviews"].append({"category": category, "review": review})
                    _track("deep_review", None, category=category, review_snippet=review[:200])
        finally:
            conn.close()
    except Exception as e:
        _track("deep_failed", e)
    return stats
