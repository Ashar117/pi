# CHECKPOINT — phase-0-complete

**Phase:** 0 — Reconciliation (read-only)
**Session ID:** N/A (no `pi_agent.py` runs in this session)
**Date / time:** 2026-04-25 (cold-start session, no prior `current.md`)
**Duration:** ~1 sustained working pass (this session)

## Did

- Created [CHECKPOINTS/](CHECKPOINTS/) directory.
- Read every `.md`, `.txt`, `.py`, `.sql`, `.json`, and a sample of `.jsonl` files in the repo (excluding `pi_env/`, `.git/`, `__pycache__/`, `local_models/`, `data/pi.db`, the body of `pi_dna.txt` due to size).
- Built the import graph by `grep`-ing every `from`/`import` line across `**/*.py`.
- Sampled the tail of `logs/evolution.jsonl` and `logs/patterns.jsonl` to ground claims about runtime behaviour against actual log entries (rather than reasoning about what the code "would" do).
- Produced the six Phase-0 deliverables.

## Verified

- **Tools are wired, contrary to four stale docs.** Claude tool loop at [pi_agent.py:454-482](pi_agent.py#L454-L482); 8 tools defined at [pi_agent.py:140-238](pi_agent.py#L140-L238); confirmed by `tools_used: ["memory_read", "memory_read"]` in `logs/evolution.jsonl` 2026-04-25 02:16:52.
- **Session ID propagates.** All five tail entries of `logs/evolution.jsonl` from session `bfe9f64b` carry `metadata.session_id = "bfe9f64b"`.
- **Evolution telemetry drift confirmed.** [evolution.py:48](evolution.py#L48) writes `tools_used`; [evolution.py:90](evolution.py#L90) reads `tool_calls`. Latest log entries do not contain a `tool_calls` field — analytics silently empty.
- **`memory_read(tier=None)` excludes L1.** [tools/tools_memory.py:96](tools/tools_memory.py#L96) confirmed.
- **Dead code modules confirmed.** No `*.py` file imports `llm.routing` or `app.state` (verified by grep across the whole tree). `llm/routing.py:139` hard-codes wrong model `claude-haiku-4-6`. `app/state.py:init_db` creates 10 tables none of which `tools_memory.py` reads or writes.
- **Stale-doc files referenced in fix specs do not exist.** `memory/sqlite_store.py`, `memory/supabase_store.py`, `memory/l3_builder.py`, `app/main.py`, `llm/tools.py`, `llm/tool_executor.py` — all absent (confirmed via Glob and `ls`).
- **Tests do not exercise the real tool loop.** [testing/](testing/) — every memory test calls `MemoryTools` directly. None instantiate `PiAgent` and feed input through `process_input`. The exact failure mode in production (LOG1/LOG2) is uncovered.

## Modified

- [CHECKPOINTS/phase-0-complete.md](CHECKPOINTS/phase-0-complete.md) — new (this file).
- [CHECKPOINTS/current.md](CHECKPOINTS/current.md) — new, points at this checkpoint.
- [RECONCILIATION.md](RECONCILIATION.md) — new. Doc-by-doc table.
- [FILE_INVENTORY.md](FILE_INVENTORY.md) — new. Per-`.py` import graph + status.
- [CONTRADICTIONS.md](CONTRADICTIONS.md) — new. 12 contradictions with citations.
- [DEAD_CODE.md](DEAD_CODE.md) — new. 9 candidates with questions for Ash.
- [SCHEMA_MISMATCHES.md](SCHEMA_MISMATCHES.md) — new. 6 entries; SM-001 is P0.
- [STATUS.md](STATUS.md) — new. One-page synthesis.

No code files modified. No files moved. No files deleted. Per master prompt §2.4, this entire phase was read-only.

## Blocked / Open

- **`pi_dna.txt` (167 KB)** was not read in full this session. It's flagged in [DEAD_CODE.md DC-008](DEAD_CODE.md) for Phase 1 — needs paging through to extract any salvageable templates (master prompt §6.3 mentions a `MODULE_TEMPLATE.py` in §18) before archive.
- **`archive_old_docs/`** is empty. Question for Ash: repurpose as `docs/_archive/2026-04-25/` (master prompt §6.1 destination), or remove and create the prompt-specified path.
- **`tickets/open/`** is empty. Open tickets currently live in [analysis/tickets.jsonl](analysis/tickets.jsonl) (T-017, T-018, T-019). Phase 1 should promote these to `tickets/open/T-XXX-slug.json` per [analysis/WORKFLOW.md:70-72](analysis/WORKFLOW.md#L70-L72).

No three-strike blockers. No ambiguous code intent that requires Ash's judgment before Phase 1 can begin.

## Next session's first step

Send Ash the Phase 0 acceptance ask:
> Phase 0 complete. Read `STATUS.md` and `CONTRADICTIONS.md`. Say "phase 1" when ready to begin the docs-collapse sweep, or flag any contradiction you'd like resolved differently than proposed.

Per master prompt §6 Phase 0 acceptance gate: do not proceed to Phase 1 without that explicit confirmation.

## Notes to self

- The four stale fix docs (`ARCHITECTURE_FIX.md`, `CRITICAL_FIX_TICKET.md`, `ARCHITECTURE_ADDENDUM.md`, `VSCODE_CLAUDE_PROMPT.md`) are coherent as a *plan*. They describe a fix that was never executed. They were likely all written in the same session before the actual fix was applied via `pi_agent.py` instead. Worth noting in the Phase 1 archive `README.md` so future-Ash (or future-me) understands the timeline.
- The capability table in [ABOUT.md:30-43](ABOUT.md#L30-L43) is the single most prominent overclaim in the public-facing surface. Phase 1 should prioritise getting that table truthful — even if `docs/ARCHITECTURE.md` is still being merged. Honest table > completed merge.
- Master prompt §1.10 calls `analysis/` "the most honest source of truth in the repo." That stood up under audit — every claim in `analysis/SUMMARY.md`, `WORKFLOW.md`, and `tickets.jsonl` is consistent with the code. Trust those during Phase 1+.
- The latest `logs/evolution.jsonl` entries (2026-04-25 02:11–02:16) confirm Pi was used in a fresh session today (session `bfe9f64b`). Some of those interactions involved real `memory_read` tool calls. So the codebase is actively exercised, not just inert.
