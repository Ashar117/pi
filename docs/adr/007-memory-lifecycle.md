# ADR-007: Memory lifecycle

**Date**: 2026-05-26
**Status**: Accepted (final — all three stages shipped)
**Tickets**: T-125a (derived facts), T-125b (dedup), T-125c (contradictions + this finalisation)
**Predecessors**: T-078 (invalid_at), T-080 (embedding dedup), T-109 (retention engine)

## Context

Pi's L3 cache started life as append-only. Three problems compounded:

1. **Derived facts go stale.** "User is 19" stays 19 forever, even after a birthday.
2. **Near-duplicates accumulate.** "User likes coffee" + "User loves coffee" become independent facts; search noise grows.
3. **Contradictions coexist.** "User lives in Atlanta" + "User lives in Multan" both rank in recall; the latest fact is not authoritative.

T-078 added `invalid_at` for soft-superseded facts. T-080 added an embedding dedup pass. Both were one-off jobs. The caretaker generalises them into a continuous lifecycle.

## Decision — four lifecycle states

| State | Marker | Meaning | Search-visible? |
|---|---|---|---|
| `stated` | `kind` IS NULL | A raw fact written by user/agent (default) | yes |
| `derived` | `kind` = 'derived' | Computed from a `source_id` row via a `formula` | yes |
| `superseded` | `superseded_by` set | Replaced by a near-duplicate (T-125b dedup); kept for audit | no |
| `invalidated` | `invalid_at` set | Contradicted by a newer fact (T-125c) OR explicitly superseded (T-078); kept for audit | no |

Schema extension (idempotent migration in `tools_memory.py`):

```sql
ALTER TABLE l3_cache ADD COLUMN invalid_at TEXT;       -- T-078
ALTER TABLE l3_cache ADD COLUMN kind TEXT;             -- T-125a
ALTER TABLE l3_cache ADD COLUMN source_id TEXT;        -- T-125a
ALTER TABLE l3_cache ADD COLUMN recompute_after TEXT;  -- T-125a
ALTER TABLE l3_cache ADD COLUMN formula TEXT;          -- T-125a
ALTER TABLE l3_cache ADD COLUMN superseded_by TEXT;    -- T-125b
```

## State transitions

```text
                              ┌─────────────┐
                              │   STATED    │  (default — kind IS NULL)
                              └──────┬──────┘
                                     │
                  ┌──────────────────┼──────────────────┐
                  │                  │                  │
       memory_write detects   caretaker.full()   user contradicts:
       'born YYYY-MM-DD'      finds cosine≥0.92  same topic, new value
                  │                  │                  │
                  ▼                  ▼                  ▼
            ┌──────────┐      ┌────────────┐      ┌─────────────┐
            │ DERIVED  │      │ SUPERSEDED │      │ INVALIDATED │
            │ (paired) │      │ (audit)    │      │ (audit)     │
            └──────────┘      └────────────┘      └─────────────┘
                  │
   caretaker.lite() recomputes
   when recompute_after < now
```

## Caretaker modes

Three modes; each is idempotent and soft-mutation only.

| Mode | What it does | When it runs | Cost |
|---|---|---|---|
| `lite(db)` | Recompute derived facts whose `recompute_after <= now` | per-bubble close (TelegramTools), session-exit, daily cron | <50ms typical |
| `full(db)` | lite + embedding dedup (cosine ≥ 0.92, same category) + contradiction scan (same topic key, different value, newest wins) | session-exit (10s budget), daily cron | <30s on 1000-row L3 |
| `deep(db)` | Haiku-backed pattern review per category — surfaces edge cases the heuristic missed | daily cron only | ~5s + Haiku cost |

`filelock` on `data/caretaker.lock` serialises bubble + cron + session-exit so they cannot race.

## Topic-key heuristic (T-125c)

Contradiction detection groups rows by `(category, topic_key)` and flags when the same key has multiple distinct values. Topic key is:

1. **Known relation verbs first**: "lives in", "works at", "studies at", "based in", "born in", "married to", "located in", "going to" — these are the high-precision cases.
2. **Fallback**: first 2 non-stopword tokens (stopword list includes "user", "the", "is", "lives", "loves", "borns", etc.).

The `_value_tail` helper extracts the value-side of a relation statement so "Atlanta" vs "Multan" is detected as a real conflict rather than topical similarity. Rows with identical values are not flagged.

## Wiring

- **`memory_write`** (tools/tools_memory.py): on every L3 write, `detect_derivable(content)` is called. If a birthday-like pattern is found, a paired derived row is spawned with `recompute_after = now`. Caretaker recomputes on next trigger.
- **Telegram bubble dispatch** (tools/tools_telegram.py): after each closed bubble, `caretaker.lite(agent.memory.sqlite_path)` runs. Idempotent. Failure is swallowed.
- **Session exit** (agent/session.py:EXIT_STEPS): adds `caretaker_full` step with a 10s budget. Daemon thread bounded.
- **Daily cron** (scripts/retention_tick.py): runs `full(db)` + `deep(db)` after retention policies. Output is logged but does not affect retention's exit code.
- **Search paths** (memory_read, _hybrid_search_l3, _l3_fast_path, _search_l3_cache, get_l3_context, memory/recall): all filter `superseded_by IS NULL` and `invalid_at IS NULL` so dead facts never surface in answers.

## Consequences

**Good**:
- Derived facts auto-refresh; no manual age bumps.
- All mutations are SOFT — `superseded_by` and `invalid_at` are set; rows stay in DB. Rolling back a wrongly-merged pair is `UPDATE superseded_by = NULL`.
- Filelock prevents the bubble path from racing the nightly cron.
- Backfill is idempotent — running it 100 times spawns the same set of derived rows once.
- Deep mode is informational only — no automatic mutations from Haiku output, so a bad model response cannot corrupt memory.

**Trade-offs**:
- Schema grows by 6 columns per row; cost is per-row TEXT, negligible.
- Topic-key heuristic is verb-list-based; misses creative phrasings ("I'm now in X" without the verb "live"). Acceptable for v1.
- Contradiction scan trusts newest-wins; a brief mistake ("typo: Atlana") would invalidate the correct older value. Mitigation: deep-mode Haiku catches these as "looks like a typo" suggestions in the next nightly review.
- Same-category requirement for dedup AND contradictions is the cheapest false-positive defence; cross-category cases (same fact under "note" and "profile") slip through. Documented as future work.

## Rejected alternatives

- **Recompute every read.** Too expensive at scale; would block memory_read.
- **Mutable stated rows.** Updating `User is 19` in place without an explicit derived row loses audit history.
- **Cron-only (no per-bubble).** Stale by up to 24h; a user celebrating their birthday in chat would have to wait for nightly.
- **Embedding-based topic detection.** Heavy for write-path; verb-pattern + stopword fallback covers the common cases.
- **Hard delete of superseded rows.** Loses audit. Storage cost is tiny; readability is preserved via search-path filters.
- **NER for contradiction value extraction.** Considered for "User is 19" → "19" → conflict detect. Rejected as overkill; numeric conflicts are rare relative to relation-verb conflicts.

## Failure modes + recovery

| Failure | Recovery |
|---|---|
| Caretaker.lite crashes mid-recompute | Filelock released in `finally`; next trigger retries. Partial writes commit per-row, so progress is preserved. |
| Caretaker.full produces a false merge | `UPDATE l3_cache SET superseded_by = NULL WHERE superseded_by = '<winner>'` resurrects all losers. Soft-only mutation makes this trivial. |
| Caretaker.full produces a false invalidation | Same shape: `UPDATE l3_cache SET invalid_at = NULL WHERE invalid_at IS NOT NULL AND id = '<row>'` |
| Filelock stuck (process died holding it) | Lock has 10s timeout; will retry on next trigger. Manual recovery: `rm data/caretaker.lock`. |
| Deep mode Haiku returns garbage | Output is informational only; logged via track_silent.deep_review. No state change. |

## Related work

- T-078: `invalid_at` for explicit invalidation. T-125c reuses this column for contradiction-loser marking.
- T-080: embedding dedup as one-off. T-125b generalises into continuous process.
- T-109: retention engine. Caretaker hooks `full + deep` into the daily cron as a sibling job.
- T-113: silent_failure_watcher. Surfaces `caretaker.*` events in the daily passive digest.

## Future work (not in Phase 8.8)

1. Cross-category dedup with a higher cosine threshold and category-conflict resolver.
2. Cascade invalidation: if a source row is invalidated, its derived children should be invalidated too.
3. Embedding-based topic detection for contradiction scan to handle creative phrasings.
4. Auto-promote deep-mode Haiku suggestions to tickets via `conversation_ticket_miner` (T-127) — close the observation→action loop.
