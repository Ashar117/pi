# STATUS — Pi as of 2026-04-25

**As of 2026-04-25, Pi is a working agent with a real Claude tool loop, persistent storage, session correlation, and an honest analysis pipeline — sitting under five stale docs that describe a different architecture and one silent telemetry bug that empties every self-improvement metric.**

This is the one-page summary. Citations link to source files; full evidence is in [RECONCILIATION.md](RECONCILIATION.md), [FILE_INVENTORY.md](FILE_INVENTORY.md), [CONTRADICTIONS.md](CONTRADICTIONS.md), [DEAD_CODE.md](DEAD_CODE.md), [SCHEMA_MISMATCHES.md](SCHEMA_MISMATCHES.md).

---

## What works (cite, then claim)

- **Agent tool loop.** [pi_agent.py:454-482](pi_agent.py#L454-L482) — `while response.stop_reason == "tool_use":` correctly executes tool calls, appends `tool_result` blocks, and continues the conversation. 8 tools wired ([pi_agent.py:140-238](pi_agent.py#L140-L238)). Most recent log entry confirms real calls in production: `logs/evolution.jsonl` 2026-04-25 02:16:52 → `"tools_used": ["memory_read", "memory_read"]`.
- **Mode switching, including natural-language variants.** [pi_agent.py:344-371](pi_agent.py#L344-L371) — loose matcher accepts "switch to root mode", "go normie", etc. (S-010, T-015 closed).
- **Cross-mode continuity.** [pi_agent.py:548-572](pi_agent.py#L548-L572) — both modes append to `self.messages`, so switching normie → root no longer empties Claude's view of the conversation (S-011, T-016 closed).
- **Session ID propagation.** [pi_agent.py:68](pi_agent.py#L68) — `session_id = uuid.uuid4().hex[:8]`. Visible in evolution log metadata: `metadata.session_id` consistent across consecutive entries in `logs/evolution.jsonl`. Used as L1 `thread_id` ([tools/tools_memory.py:223-228](tools/tools_memory.py#L223-L228)).
- **Safe message truncation.** [pi_agent.py:509-520](pi_agent.py#L509-L520) — walks forward to the next plain user-text message before slicing, never orphans a `tool_result` from its `tool_use` (S-009, T-012 closed).
- **L3 dynamic category injection.** [tools/tools_memory.py:329-370](tools/tools_memory.py#L329-L370) — replaced the silently-truncating hardcoded category dict with dynamic grouping; every category written to L3 now appears in context output (S-008, T-010 closed).
- **L3 sync TTL.** [tools/tools_memory.py:302-305](tools/tools_memory.py#L302-L305) — `_sync_l3` runs at most once every 300s instead of per-message (T-011 closed).
- **Verified writes mean both stores.** [tools/tools_memory.py:401-422](tools/tools_memory.py#L401-L422) — `_verify_write` confirms both SQLite cache and Supabase row exist; `verified=True` only when both pass (S-008, T-014 closed).
- **L1 raw_wiki write path.** [tools/tools_memory.py:220-238](tools/tools_memory.py#L220-L238) — `tier="l1"` writes work (T-008 closed).
- **Session summary on exit.** [pi_agent.py:766-777](pi_agent.py#L766-L777) — exit triggers Groq-generated summary, written to L3 with `category="session_history"` (S-006, T-007 closed).
- **Health check.** [pi_agent.py:629-655](pi_agent.py#L629-L655) — verifies Supabase, SQLite, Anthropic key, Groq key, Supabase key on startup.
- **Conversation analysis pipeline.** [analysis/](analysis/) — chat logs → tickets → SUMMARY synthesis. Operating; T-015–T-019 generated through it.
- **Engineering loop infrastructure.** [tickets/closed/](tickets/closed/) (11 closed tickets), [solutions/SOLUTIONS.jsonl](solutions/SOLUTIONS.jsonl) (S-006 to S-011), [solutions/LESSONS.md](solutions/LESSONS.md) (L-001 to L-010). Honest, append-only, useful.

## What's broken (cite, then claim)

- **Evolution telemetry analytics are silently empty.** [evolution.py:48 (write)](evolution.py#L48) emits `tools_used`; [evolution.py:90 (read)](evolution.py#L90) reads `tool_calls`. The two field names never agree, so `analyze_performance().tool_usage` and `tool_success_rates` always return `{}`. `_performance_report` ([pi_agent.py:589-627](pi_agent.py#L589-L627)) and the monthly-review improvement pipeline ([pi_agent.py:657-714](pi_agent.py#L657-L714)) operate on empty data and have been since the analyzer was written. Documented as SM-001 in [SCHEMA_MISMATCHES.md](SCHEMA_MISMATCHES.md). **This is the single highest-impact bug in the repo.**
- **`memory_read(tier=None)` excludes L1 despite docstring.** [tools/tools_memory.py:96](tools/tools_memory.py#L96) — open ticket [T-017](analysis/tickets.jsonl). SM-004.
- **L2 search is title-only.** [tools/tools_memory.py:87](tools/tools_memory.py#L87) — content stored at `content.text` (JSONB) is unsearchable via `memory_read(tier="l2")`. Acknowledged in [ARCHITECTURE_DIRECTION.md:368-369](ARCHITECTURE_DIRECTION.md#L368-L369). SM-003.
- **Normie mode prompt-side hallucination.** Open ticket [T-019](analysis/tickets.jsonl) — when asked to remember in normie mode, Pi sometimes claims persistence happened. Even with T-015 fixed (loose mode-switch detection), the consciousness prompt still allows the LLM to mime tool effects when none are available.

## What's unverified (the dangerous middle)

- **Memory round-trip via the real tool loop.** [testing/](testing/) has 18 tests across 4 suites. **Zero of them** invoke `PiAgent.process_input()` and verify Claude actually issues a `memory_write` tool_use block, then on rebuild retrieves it via `memory_read`. All memory tests call `MemoryTools` directly. The thing that broke in LOG1/LOG2 (the LLM saying "I've stored…" without a tool call) is exactly what isn't covered. Master prompt §6 Phase 3 owns this.
- **session_id-grouped log queries.** S-009 says session_id propagates; latest log entries confirm it appears in `metadata.session_id`. But there's no test that asserts all entries from a given session share the same id. Master prompt §6 Phase 3 includes this.
- **Mode-switch tests are string-grep tests, not behaviour tests.** [testing/test_modes.py:18-104](testing/test_modes.py#L18-L104) only checks that `pi_agent.py` *contains* certain substrings ("MODE: ROOT", "_respond_normie", etc.). It does not start an agent, send a natural-language switch phrase, and assert `self.mode` flipped. Master prompt §6 Phase 5 owns this.

## Documentation surface area

- **STALE root docs (4)**: [ARCHITECTURE_FIX.md](ARCHITECTURE_FIX.md), [CRITICAL_FIX_TICKET.md](CRITICAL_FIX_TICKET.md), [ARCHITECTURE_ADDENDUM.md](ARCHITECTURE_ADDENDUM.md), [VSCODE_CLAUDE_PROMPT.md](VSCODE_CLAUDE_PROMPT.md). All four describe a fix path through `llm/` and `memory/` modules that was never built. The actual fix landed in `pi_agent.py`.
- **SUPERSEDED root docs (5)**: [VSCODE_MASTER_PROMPT.txt](VSCODE_MASTER_PROMPT.txt), [DEPLOYMENT_PROTOCOL.txt](DEPLOYMENT_PROTOCOL.txt), [EXECUTIVE_SUMMARY.txt](EXECUTIVE_SUMMARY.txt), [TESTING_FRAMEWORK.txt](TESTING_FRAMEWORK.txt), [FAILURE_TICKETS.txt](FAILURE_TICKETS.txt). The 4-document, 5-ticket fix sprint they describe has been replaced by the engineering loop in `analysis/` + `solutions/` + `tickets/`.
- **CANONICAL with overclaim (2)**: [README.md](README.md), [ABOUT.md](ABOUT.md). Capability tables list every capability as ✅ Working; needs to be downgraded to 🟡 for items not verified end-to-end.
- **CANONICAL (clean)**: [ARCHITECTURE.md](ARCHITECTURE.md), [ARCHITECTURE_DIRECTION.md](ARCHITECTURE_DIRECTION.md), [USER_GUIDE.md](USER_GUIDE.md), all of [analysis/](analysis/), all of [solutions/](solutions/), all closed tickets, [SUPABASE_SETUP.sql](SUPABASE_SETUP.sql), [data/README.md](data/README.md), [.gitignore](.gitignore), [requirements.txt](requirements.txt), [LICENSE](LICENSE), [PI_MASTER_PROMPT.md](PI_MASTER_PROMPT.md).

Headline: **9 of 18 root-level docs are STALE or SUPERSEDED.** The Phase 1 archive sweep is the single highest-leverage doc-cleanup task in the project.

## Code surface area

- **6 LIVE Python modules** form the runtime: [pi_agent.py](pi_agent.py), [tools/tools_memory.py](tools/tools_memory.py), [tools/tools_execution.py](tools/tools_execution.py), [evolution.py](evolution.py), [app/config.py](app/config.py), [core/research_mode.py](core/research_mode.py).
- **2 DEAD modules** in the runtime path: [llm/routing.py](llm/routing.py) (no importers; uses wrong model `claude-haiku-4-6`), [app/state.py](app/state.py) (no importers; defines 10 unused tables). Both addressable in master prompt Phase 4.
- **All test files (7) are subprocess-isolated** but **none exercise the real Claude tool loop** — that gap is exactly what Phase 3 fills.

## What Phase 0 produced

- [RECONCILIATION.md](RECONCILIATION.md) — 21-row table mapping every doc to (status, claims, runtime reality).
- [FILE_INVENTORY.md](FILE_INVENTORY.md) — every `.py` with importers/imports and LIVE / DEAD / STUB / TEST / EXEC status.
- [CONTRADICTIONS.md](CONTRADICTIONS.md) — 12 contradictions with source A, source B, runtime truth, resolution, action.
- [DEAD_CODE.md](DEAD_CODE.md) — 9 dead-code candidates with import-graph evidence and a question for Ash before each archive.
- [SCHEMA_MISMATCHES.md](SCHEMA_MISMATCHES.md) — 6 schema drifts, including SM-001 (the silently-empty analytics).
- This file ([STATUS.md](STATUS.md)) — the one-page synthesis.

## Recommended order of operations (matches master prompt §6)

1. **Phase 1 — docs collapse.** Archive 9 root docs, merge architecture, rewrite README + ABOUT capability tables to honest claims. Highest leverage, lowest risk.
2. **Phase 2 — fix SM-001.** One-line fix in `evolution.py`, one new schema test. Unblocks every analytic.
3. **Phase 3 — memory round-trip test.** The single missing test that would have caught LOG1/LOG2 in regression. Fix T-017 (docstring) and L2 content search alongside.
4. **Phase 4 — modular refactor + dead-code archive.** Split `pi_agent.py` mechanically; archive `llm/` and `app/state.py`.
5. **Phase 5 — prompt-engineering pass.** Fix C-008 (system.txt overclaim), C-009 (web_search ghost tool), T-019 (normie hallucination).
6. **Phase 6 — CI + contributing protocol.** `scripts/verify.py`, templates, CHANGELOG cadence.

Phase 0 is complete. Awaiting Ash's "phase 1" to proceed.
