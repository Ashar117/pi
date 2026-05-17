# ADR-005 — Resumable session exit; move 5 of 8 ops out of `on_exit`

**Status:** Proposed (awaiting Ash sign-off)
**Date:** 2026-05-17
**Ticket:** [T-085](../../tickets/open/T-085-r4-resumable-session-exit.json) (R4 of Hardening Track)
**Author:** Claude (Opus 4.7)

ADR numbering note: this is `005` not `004` per the ticket spec — R5 (T-086,
sprint × god isolation) reserves 004.

---

## Decision

`agent/session.py::on_exit` is rebuilt around a **resumable state file**
(`data/session_exit_state.json`) and reduced from 8 ops to **3 must-do-at-exit**
ops. The other 5 either move into **mid-session** (where they can run more
than once per session, idempotently) or onto **cron** via
`scripts/passive/*.py` (where they run on a schedule independent of session
lifecycle).

Concretely:

**`on_exit` after R4** (3 ops):

1. `flush_logs` — drain `_log_queue` so async-logged turns land in Supabase
2. `session_summary` — Groq summary written to L3 (cheap, fast, only-at-exit
   makes sense because the summary describes the session as a whole)
3. `finalize_exit_state` — write `data/session_exit_state.json` with
   `status='completed'` so the daemon's next startup knows there is nothing
   to resume

**Moved to mid-session** (run inside `_maybe_mid_session_distill`,
already firing every 10 turns):

4. **L2 → L3 promotion** — currently exit-only. Result: a high-importance
   L2 fact written this session is invisible to next session's ambient
   context until the *current* session exits cleanly. Moving it
   mid-session means it's promoted within 10 turns of being written.
5. **Vault sync** (per-fact L2 + entity hubs) — S-056's `better_future_fix`
   already called this out. Mid-session sync means the Foam graph updates
   while the user is still working. Cost is bounded — L2 row count is
   small, the bottleneck is Supabase round-trip.

**Moved to cron** (daily/weekly via `scripts/passive/`):

6. **L3 expired prune** — `scripts/passive/memory_prune.py`, runs **daily**
   via PiScheduler. Has no real-time correctness requirement; pruning
   expired rows tomorrow morning is fine. Registered alongside the
   existing `l3_prune` job in `tools/tools_scheduler.py`.
7. **L2 stale prune** — same script, same daily slot. Combined into one
   pass for IO efficiency.
8. **Weekly memory audit** — `scripts/passive/weekly_memory_audit.py`,
   runs **weekly** (Sunday 02:00 local). `should_run_weekly()` gate moved
   inside the script. Telegram digest stays.

**Resumable state machine:**

```json
// data/session_exit_state.json (rewritten on every step transition)
{
  "session_id": "abc12345",
  "started_at": "2026-05-17T01:30:00+00:00",
  "completed_at": null,         // set when finalize step runs
  "steps": [
    {"name": "flush_logs",       "status": "completed",  "completed_at": "...", "error": null},
    {"name": "session_summary",  "status": "in_progress","started_at": "...",   "error": null},
    {"name": "finalize",         "status": "pending",    "error": null}
  ]
}
```

**Resume logic** (in `pi_daemon.py::_get_agent`, before `server.listen`):

```python
def _resume_exit_if_needed():
    state = _load_exit_state()  # returns None if no file or already completed
    if state is None:
        return
    pending = [s for s in state["steps"] if s["status"] in ("pending", "in_progress")]
    if not pending:
        return
    print(f"[Daemon] Resuming {len(pending)} unfinished exit step(s)...")
    from agent.session import resume_exit
    resume_exit(_agent, state)  # idempotent re-run of pending steps
```

Each step is independently idempotent:
- `flush_logs` is naturally idempotent (drains an empty queue if already drained)
- `session_summary` re-writes to L3 with the same `session_id` — semantic dedup
  catches the duplicate via T-080 cosine
- L2 → L3 promotion uses `invalid_at` (S-054); re-promoting a row sets the same
  invalid_at, no-op
- Vault sync is rewrite-the-files; rerunning produces identical output
- Prunes are naturally idempotent (deleting an already-deleted row is a no-op)
- Audit is gated by `should_run_weekly()` — re-running same week skips

So resume is safe even when called multiple times.

## Context

`agent/session.py::on_exit` today runs 8 ops sequentially:

```
flush_logs → session_summary → distill L1→L2 → promote L2→L3 →
prune L3 expired → prune L2 stale → weekly_audit (gated) → vault_sync
```

Each wrapped in `try/except` with "non-fatal" log. Mid-session distillation
(T-072) partially covers L1→L2, but everything else is exit-only.

**The structural problem:** Exit-heavy workflows assume clean shutdown. Real
systems crash. Real users SIGKILL. Each non-fatal try/except masks the
cumulative loss because *no one notices* — the session ends, the user moves
on. Concrete failure modes observed or possible:

- **SIGKILL mid-promote** → high-importance L2 fact written this session
  doesn't reach L3 ambient context. Tomorrow's session starts without it.
  User asks "what did I tell you yesterday about X" — Pi doesn't recall
  because X never got promoted.
- **Daemon OOM mid-vault-sync** → Foam graph is half-updated, audit digest
  half-written. Next session sees partial state, possibly with broken
  wikilinks pointing at non-existent slugs.
- **Power loss mid-audit** → `audit_state.json` not updated, next exit
  re-runs the audit and re-Telegrams the same findings.

Exit duration today: typically 5-15 seconds (varies with L1 size). User
perception: "Pi takes forever to quit." The vault sync alone is 2-4s; the
audit when it fires is 10-30s; distillation is 2-5s.

**The mid-session-or-cron pivot is the real win.** Resumable state alone
helps but doesn't solve the perceived-latency problem. Moving promotion +
vault sync into mid-session means:
- Promotion fires within ~10 turns of an L2 write → faster feedback loop
- Vault sync runs while user is working → no exit penalty
- Audit + prunes run on cron → exit-independent, predictable schedule

## Alternatives considered

### A1 — Resumable state only; keep all 8 ops at exit

**Pros:** Simplest. Doesn't change *when* anything runs, just makes the
sequence resumable on crash.
**Cons:** Doesn't address the perceived-latency problem. The user still
waits 5-15s on exit. SIGKILL on a typical exit still loses ~half the work
because resume runs on *next startup* not before the user closes the
terminal.
**Rejected.** Crash safety alone isn't enough — the latency was already a
complaint surfaced informally during T-077 work.

### A2 — Move everything to cron; on_exit becomes a no-op

**Pros:** Maximum simplicity for the exit path.
**Cons:** Loses the *contextual* value of session-summary-at-exit (the
summary describes *this* session at *this* moment; a daily cron summary
loses the boundary). Also: flush_logs MUST run at exit to drain the
in-process async queue — that's not movable.
**Rejected — too aggressive.**

### A3 — Async exit (background thread + return immediately)

```python
def on_exit(agent):
    threading.Thread(target=_run_all_exit_ops, daemon=True).start()
    return  # user gets prompt back immediately
```

**Pros:** Zero perceived latency.
**Cons:** Daemon thread dies when main process exits — half-finished work
discarded silently. Could use a *non-daemon* thread, but then `EXIT`
doesn't actually exit until the thread finishes (same wait, different
shape). Worst of both.
**Rejected.**

### A4 — This ADR's plan: resumable + mid-session + cron split

**Pros:** Addresses both correctness (resumable) and latency (3-op exit).
Each piece has a sensible home for *when* it runs. Mid-session promotion
gives faster ambient-context feedback. Cron prunes/audit decouple from
session boundaries.
**Cons:** Most files touched. Need to ensure mid-session ops are idempotent
and concurrent-safe (`_maybe_mid_session_distill` runs in a background
thread; vault sync writes need a lock to avoid two threads stomping each
other).
**Selected.**

## Consequences

### Positive

- **Exit ≤ 3 ops, ≤ 2 seconds typical** (estimate: flush 0.5s + summary
  1s + finalize 0.1s). Down from 5-15s. Success criterion: ≥50% drop.
- **Crash safety.** Daemon SIGKILL → next startup completes remaining
  steps before accepting connections. No silent memory degradation.
- **Faster ambient-context feedback.** L2 → L3 promotion within ~10
  turns instead of at-exit. High-importance fact written this turn
  becomes visible in L3 context in the same session.
- **Cron decoupling.** Audit + prunes run on a known schedule, not
  "whenever someone exits Pi." Predictable for monitoring and easier to
  reason about ("when was the last audit" answered by reading
  `audit_state.json`, not "did anyone exit cleanly last week").
- **Each step independently testable.** State file format makes
  per-step assertions trivial in tests.

### Negative / risks

- **Mid-session concurrency.** `_maybe_mid_session_distill` runs in a
  background thread. Adding L2→L3 promotion + vault sync there means
  two threads could both promote/sync if turns 10 and 20 fire close in
  time. Mitigation: a `_promote_lock` and `_vault_sync_lock` on
  `MemoryTools` / `ObsidianTools`. Cheap; non-blocking on the response
  path.
- **State file as new failure surface.** If `data/session_exit_state.json`
  is corrupted, the resume logic must fail-open (skip resume, log
  warning, continue startup). Documented in the resume function
  docstring. Worst case: one session's worth of exit ops doesn't get
  resumed — same as the current "non-fatal" exception path.
- **Cron job scheduling depends on daemon being up.** If Pi daemon was
  off all weekend, the weekly audit doesn't fire until next time daemon
  runs and the scheduler's next-Sunday check trips. Acceptable — Pi
  daemon is up most of the time on Ash's box. For belt-and-suspenders,
  `should_run_weekly()` checks the last run time so "missed a week"
  surfaces explicitly next startup.
- **Two new passive scripts** (`memory_prune.py`,
  `weekly_memory_audit.py`) join the 12 existing ones. Pattern is
  established; just two more files in `scripts/passive/`.

### Neutral

- `agent/session.py::on_exit` stays as the public entry point; its body
  shrinks. Signature unchanged.
- `data/` is already gitignored — `session_exit_state.json` lives there
  naturally with no privacy implications.

## Migration plan (one step = one commit-or-tight-batch)

1. **This ADR.** Sign-off before code.
2. Define `_ExitState` writer class in `agent/session.py`. Atomic
   `data/session_exit_state.json` writes (write to `.tmp`, rename).
   Schema documented in module docstring per the ticket criterion.
3. Refactor `on_exit` to use `_ExitState`: each existing step updates
   state before/after. No behavior change yet — all 8 ops still run at
   exit. Just adds the state-tracking layer.
4. Add `resume_exit_if_needed()` in `agent/session.py`; call it from
   `pi_daemon._get_agent()` *after* PiAgent init but *before* the
   `server.listen()` line. State file's `completed_at` field is the
   "nothing to resume" signal.
5. Move L2→L3 promotion: call `agent.memory.promote_l2_to_l3()` from
   `pi_agent._maybe_mid_session_distill` after the existing
   `distill_session` call. Remove the call from `on_exit`. Add
   `MemoryTools._promote_lock` for concurrency safety.
6. Move vault sync: call `sync_vault(agent.memory)` from
   `_maybe_mid_session_distill` (in addition to remaining call in
   on_exit for the final-mile sync of session-summary etc). Add
   `_vault_sync_lock` in `tools_obsidian.sync_vault`.
7. Create `scripts/passive/memory_prune.py` — standalone script that
   calls `agent.memory.prune_l3_expired()` + `prune_l2_stale()`.
   Register with PiScheduler as `add_daily("04:00", ...)`. Remove the
   prune calls from `on_exit`.
8. Create `scripts/passive/weekly_memory_audit.py` — extracts the
   audit block from `on_exit`. Move `should_run_weekly()` check into
   the script. Register with PiScheduler as a weekly job (Sunday 02:00).
9. Trim `on_exit` body to the 3 final ops: flush_logs → session_summary
   → finalize_exit_state.
10. Write `tests/test_resumable_exit.py`: (a) simulate mid-exit crash
    by manually marking a step `in_progress`; (b) call
    `resume_exit_if_needed()`; (c) assert remaining step runs and state
    transitions to `completed`. Plus a test that verifies on_exit body
    is ≤3 ops via AST inspection of agent/session.py.
11. Append `SOLUTIONS.jsonl` (S-064 or next). Note ticket success
    criteria met: exit ≤3 ops, ≥50% latency drop, scripts/passive/ has
    the two new scripts, state file schema documented.

## Open questions

- **Should `_maybe_mid_session_distill` get renamed?** It now does more
  than distillation. Candidate: `_maybe_mid_session_pulse`. Out of
  scope; mention in the solution entry as a future small-ticket.
- **PiScheduler weekly slot.** The existing scheduler has daily slots
  (briefing, l3_prune). Adding weekly needs the `schedule` library's
  `every().sunday.at("02:00")`. Already supported by `schedule`; no
  new dep.
- **Should vault sync at exit be dropped entirely?** Right now keeping
  it as a final-mile catch-up for session-summary. Open question
  whether that's redundant after mid-session vault sync runs. Leaving
  in for safety; can drop in a future cleanup once we have a few
  sessions of evidence that mid-session covers everything.

## Sign-off

- [ ] Ash — read and agree before step 2 begins.
