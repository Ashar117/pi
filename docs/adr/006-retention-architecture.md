# ADR-006: Retention Architecture

**Date**: 2026-05-24  
**Status**: Accepted  
**Ticket**: T-109

## Context

Five Pi data sources grow without bound:

| Source | Growth rate | Symptom at 6 months |
|--------|-------------|---------------------|
| `logs/turns.jsonl` | ~1 KB/turn, ~50 turns/day | 90 MB; OOM on full scans |
| `logs/evolution.jsonl` | ~200 B/interaction | 36 MB; slow grep |
| `data/watchers.db::watcher_events` | 10 watchers × 1/min = 14 400 rows/day | 5 M rows/year; slow queries |
| `data/memory_replication.log` | variable, spikes on L3 sync | unbounded on error storms |
| `data/pi.db` (L3 SQLite) | WAL grows after deletes/updates | WAL > main DB; slow writes |

No shared retention abstraction existed. Each source would need its own ad-hoc fix with its own edge cases.

## Decision

Introduce `agent/retention.py` — a single policy engine that:

1. Declares intent via a `Policy` dataclass (what, when, how).
2. Dispatches to kind-specific handlers (`jsonl_rotate`, `sqlite_table_prune`, `log_size_rotate`, `sqlite_vacuum`).
3. Persists run state to `data/retention_state.json` under a cross-process filelock.
4. Never deletes source data before archiving (crash-safe ordering).
5. Supports a `dry_run` flag for safe inspection.

## Policy choices

| Policy | Kind | Cadence | Rationale |
|--------|------|---------|-----------|
| `turns_jsonl` | jsonl_rotate | daily, keep 90 archives | 90 days covers any debugging window; older turns are rarely needed |
| `evolution_jsonl` | jsonl_rotate | daily, keep 180 archives | Evolution history is lighter and has longer audit value |
| `watcher_events_prune` | sqlite_table_prune, 30 days | daily | Watcher events are operational noise; 30 days covers any replay need |
| `memory_replication_log` | log_size_rotate, 50 MB cap | daily | Error storms can spike this log; 50 MB limit prevents disk pressure |
| `pi_db_vacuum` | sqlite_vacuum | weekly | WAL reclaim is cheap; weekly is aggressive enough without fragmenting writes |

## Consequences

**Good**:
- All five sources are covered by one consistent abstraction.
- `dry_run=True` enables safe auditing without mutations.
- State file makes stuck or skipped policies observable.
- Crash-safe ordering (copy → gzip → truncate) ensures no data loss if the process dies mid-rotation.
- Cross-process filelock prevents cron and session-end from racing.

**Trade-offs**:
- jsonl_rotate truncates the source file (does not append-reopen). Any writer that held an open file descriptor gets a now-empty file. Pi's log writers use `open(path, "a")` per-turn, so this is safe — each turn opens fresh.
- The filelock adds a 30 s timeout; a hung rotation would block the next retention tick. Mitigation: track_silent reports stuck policies.
- `keep_archives` counts compressed files; a long outage can skip a date, leaving a gap. Accepted: gaps are preferable to unbounded disk growth.

## Alternatives considered

- **Per-source ad-hoc rotation**: rejected — duplicates logic and edge cases.
- **External logrotate**: rejected — requires OS-level config, not portable to Windows.
- **No retention**: rejected — 5 M rows/year in watcher_events alone produces measurable query slowdown.
