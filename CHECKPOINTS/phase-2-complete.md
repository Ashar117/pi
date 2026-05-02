# CHECKPOINT — phase-2-complete

**Phase:** 2 — Evolution telemetry fix (SM-001 / T-020)
**Session ID:** N/A (no `pi_agent.py` runs in this session)
**Date:** 2026-04-25
**Duration:** continuation of the Phase-1 session

## Did

- Wrote [testing/test_evolution_schema.py](../testing/test_evolution_schema.py) — 4 tests covering: `tool_usage` populated; `session_id` at top level; per-session breakdown; legacy log entries still analyzable post-fix.
- Ran the test against pre-fix `evolution.py` to **reproduce the bug in the current codebase** before claiming closure. All 4 tests failed; output captured in chat.
- Proposed two `evolution.py` edits (gated). Ash approved with "Go".
- Applied both edits via Edit tool with exact `old_string` / `new_string` matching.
- Ran all 4 verification steps. All green:
  - V1 syntax check: `OK`
  - V2 reproduction test: 4/4 PASSED
  - V3 module import: `OK`
  - V4 patched analyzer against real `logs/evolution.jsonl` (107 interactions, 4 sessions): `tool_usage` populated with `{memory_read: 36, memory_write: 12, execute_python: 4, execute_bash: 1, read_file: 1}`; `tool_success_rates` populated with `memory_read: 0.917, memory_write: 1.0, execute_python: 1.0, execute_bash: 1.0, read_file: 1.0`; `mode_usage`: `{normie: 64, root: 43}`; 4 distinct session_ids surfaced including the `'unknown'` bucket for legacy pre-T-013 entries.
- Appended [S-012](../solutions/SOLUTIONS.jsonl) to SOLUTIONS.jsonl with the full problem / countermeasure / lessons / better-future-fix breakdown.
- Appended [L-011](../solutions/LESSONS.md) to LESSONS.md: "Telemetry field-name drift produces zero errors and infinite wrong data."
- Created [tickets/closed/T-020-evolution-schema-drift.json](../tickets/closed/T-020-evolution-schema-drift.json) with full verification block and explicit `scope_disclaimer` field — T-020 closes SM-001 only; it does not touch T-017, T-019, or the memory round-trip gap.

## Verified (with reproduction, per Ash's rule)

| Claim | Evidence |
|---|---|
| SM-001 reproduces in the current code (pre-fix) | `python testing/test_evolution_schema.py` → exit 1, all 4 tests fail. Production log analysis pre-fix returned `tool_usage: {}` |
| Patch applied syntactically clean | `python -c "import ast; ast.parse(open('evolution.py').read()); print('OK')"` → OK |
| SM-001 fixed in the current code (post-fix) | `python testing/test_evolution_schema.py` → exit 0, all 4 tests pass |
| Module imports unchanged | `from evolution import EvolutionTracker, SelfModifier` → OK |
| Real production log (107 entries) analysable post-fix | `tool_usage: {memory_read: 36, memory_write: 12, ...}`, `sessions: 4` |
| Backward compat with legacy entries | Test #4 (legacy log entries still analyzable) passes; real-log V4 surfaces `unknown` session bucket for entries that predate T-013's `session_id` propagation, all of them still contributing to `tool_usage` via the `tools_used` fallback path |

## Modified

| Path | Change |
|---|---|
| [evolution.py](../evolution.py) | Two edits — added `session_id` (top-level) and `tool_calls` (structured) fields to `log_interaction`; updated `analyze_performance` to read `tool_calls` with `tools_used` fallback and to emit a per-session `sessions` breakdown |
| [testing/test_evolution_schema.py](../testing/test_evolution_schema.py) | new — 4-test reproduction + regression suite |
| [solutions/SOLUTIONS.jsonl](../solutions/SOLUTIONS.jsonl) | appended S-012 |
| [solutions/LESSONS.md](../solutions/LESSONS.md) | appended L-011 |
| [tickets/closed/T-020-evolution-schema-drift.json](../tickets/closed/T-020-evolution-schema-drift.json) | new closed ticket with verification block |

No other code, prompts, schemas, or runtime files modified. `pi_agent.py` call sites already pass `metadata={"session_id": ...}`, which the patched `log_interaction` picks up automatically — no call-site change needed.

## Findings worth flagging (do not act this phase)

- **memory_read success rate is 91.7%** in the real production log (3 of 36 calls landed in failed interactions). That is consistent with the analysis-pipeline tickets (T-017, T-019) describing recall-side issues. It is *not* a closure of those tickets — it is a symptom that motivates Phase 3.
- **`logs/evolution.jsonl` has an `'unknown'` session bucket** because entries logged before T-013's session_id propagation never carried a session_id. That is correct, expected, and harmless — the `or 'unknown'` fallback in the new sessions-breakdown loop catches them. Just noting that the bucket exists by design.
- **`analyze_performance` does no try/except per `json.loads(line)`** — one malformed line crashes the whole report. `get_recent_interactions` does, inconsistently. Out of scope this phase; flagged in S-012's `better_future_fix` field for a future cleanup pass.

## Acceptance gate (master prompt §6 Phase 2)

> Ash sees the test output (green) pasted in chat and says "phase 3".

Test output is in the chat above this checkpoint. V4 (patched analyzer against the real production log file) is the strongest evidence — pre-fix that same call returned `tool_usage: {}`; post-fix it returns 5 distinct tools with their success rates.

## Next session's first step

Send Ash the Phase 2 acceptance ask:
> Phase 2 complete. SM-001 reproduced in current code, patched, all 4 tests pass, real production log (107 entries) now analysable. T-020 closed; T-017/T-019 still open; memory round-trip still unverified. Say "phase 3" to begin the memory round-trip work, or push back on any specific change.

## Notes to self

- Per Ash's rule (2026-04-25), only SM-001 / T-020 is closed. Everything else flagged in Phase 0 (T-017 docstring drift, T-019 normie hallucination, the round-trip gap, SM-003 L2 content search) remains *hypothesis* until reproduced in the current codebase. The S-012 `better_future_fix` field is the right place for "while we were here we noticed X" — never inflate scope mid-phase.
- The reproduction-or-it-isn't-resolved discipline added meaningful confidence here. The four tests and the real-log V4 collectively show the fix lands not just on the synthetic fixture but on the actual data the system has been collecting since 2026-04-20. Carry this discipline into Phase 3, where the memory round-trip test is structurally similar (write through tool loop, restart, read through tool loop).
- The `'unknown'` session bucket in V4 is a free piece of evidence about the project's history — it tells us, by counting, how many interactions happened before T-013 landed. If anyone ever wants to write a "Pi's growth-over-time" doc, that bucket boundary is queryable now.
