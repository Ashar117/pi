# CHECKPOINT — phase-3-complete

**Phase:** 3 — Memory round-trip verification + T-017 + L2 content search + session_id propagation verification
**Session ID:** N/A (no `pi_agent.py` runs in this session)
**Date:** 2026-04-25 → 2026-04-26 (continuation)
**Cost:** ~$0.05 (one round-trip test run; ~5 paid Claude calls)

## Did

### 3.1 — Round-trip canary
- Wrote [testing/test_memory_roundtrip.py](../testing/test_memory_roundtrip.py): instantiates `PiAgent` in-process with monkey-patched `input` to suppress monthly-review prompts; sends a write message to agent #1; tears down; rebuilds agent #2; sends recall question; reads back what got logged in `logs/evolution.jsonl` for each session_id.
- Ran once. **VERDICT: GREEN.** Pi #1 issued a real `memory_write({content: '...test_marker_88a66aeb is associated with the color purple.', tier:'l3', importance:7, category:'note'})`. Pi #2 (fresh process) replied "Purple. It's in your L3 active context." Master-prompt assertion satisfied.
- **Important caveat — see [FINDINGS.md F-001](../FINDINGS.md):** Pi #2 made *zero* tool calls. The marker was synced from Supabase to SQLite by `_sync_l3`, surfaced in `get_l3_context()` ambient context, and Claude read it directly from the system prompt. The `memory_read` *tool path* — the path the production failure mode (T-019, LOG1/LOG2) actually breaks on — was bypassed. Opened [T-023](../tickets/open/T-023-roundtrip-canary-bypasses-memory-read-pathway.json) for a follow-up test design that forces memory_read. L-012 captures the lesson.

### 3.3 — T-017 fix (docstring drift)
- Wrote [testing/test_memory_tier_contract.py](../testing/test_memory_tier_contract.py) — 3 tests covering docstring honesty + the conservative-not-aggressive code stance. Reproduced the bug pre-fix (2/3 fail).
- Proposed gated docstring edit. Ash approved with "go".
- Applied edit to [tools/tools_memory.py:50-65](../tools/tools_memory.py#L50-L65).
- All 4 verifications green: syntax OK; contract test 3/3 PASS; pre-existing `testing/test_memory.py` 5/5 PASS (no regression); module imports clean.

### 3.3 — Session_id propagation
- Wrote [testing/test_session_id_propagation.py](../testing/test_session_id_propagation.py) — 3 tests: prod-log internal consistency, in-process 5-turn simulation, and two-sessions-don't-bleed.
- Ran against current code (no fixes needed; SM-001's Phase 2 work + the existing T-013 propagation already make this work). **3/3 PASS.** Production log shows 3 multi-turn sessions: e9197064 (19 turns), 548ff561 (15 turns), bfe9f64b (8 turns) — all internally consistent. 65 legacy 'unknown' entries pre-T-013 expected and harmless.

### 3.3 — L2 content search fix (SM-003 / T-021)
- Wrote [testing/test_l2_content_search.py](../testing/test_l2_content_search.py) — writes an L2 entry with the marker past char 100 (so it's NOT in the title), searches, asserts the entry comes back. Reproduced the bug pre-fix: 0 results from both `tier='l2'` and `tier=None`.
- Probed `supabase-py 2.28.3` to confirm `.ilike("content->>text", ...)` JSON-path filter actually works against the live Supabase instance before designing the fix. It does — got 1/1 result on a probe write.
- Proposed gated edit to the L2 branch of `memory_read`. Ash approved with "go".
- Applied edit to [tools/tools_memory.py:83-110](../tools/tools_memory.py#L83-L110): two-query merge (title + content->>text), id-dedup, slice to limit. Empty-query path preserved.
- All 4 verifications green: syntax OK; SM-003 reproduction now PASS (tier='l2' and tier=None both find the entry); pre-existing `test_memory.py` 5/5 PASS; T-017 contract test 3/3 still PASS.

### Audit trail
- Appended [S-013](../solutions/SOLUTIONS.jsonl) to SOLUTIONS.jsonl: covers both T-017 docstring fix and T-021 L2 content search fix.
- Appended [L-012](../solutions/LESSONS.md) to LESSONS.md: "A round-trip test that's satisfied by ambient context isn't testing the tool path."
- Created [tickets/closed/T-017-...](../tickets/closed/T-017-memory-read-tier-none-excludes-l1.json) — promoted from analysis/tickets.jsonl, closed with verification block.
- Created [tickets/closed/T-021-...](../tickets/closed/T-021-l2-content-search-title-only.json) — opened-and-closed in this phase (SM-003 fix).
- Created [tickets/open/T-022-...](../tickets/open/T-022-windows-stdout-encoding.json) — Windows cp1252 stdout encoding (FINDINGS F-002).
- Created [tickets/open/T-023-...](../tickets/open/T-023-roundtrip-canary-bypasses-memory-read-pathway.json) — round-trip canary caveat (FINDINGS F-001).

## Verified (with reproduction, per Ash's rule)

| Claim | Pre-fix evidence | Post-fix evidence |
|---|---|---|
| T-017 docstring drift exists | testing/test_memory_tier_contract.py 2/3 FAIL | 3/3 PASS |
| T-017 conservative fix preserves code behaviour | — | testing/test_memory.py 5/5 PASS |
| Round-trip storage works (write tool → restart → recall) | — | testing/test_memory_roundtrip.py VERDICT: GREEN |
| Round-trip canary covers the memory_read tool path | — | **NO** — F-001 / T-023 |
| Session_id propagates across all entries in a single session | — | testing/test_session_id_propagation.py 3/3 PASS; prod log shows 3 multi-turn sessions internally consistent |
| SM-003 L2 content search bug exists | testing/test_l2_content_search.py FAIL with 0 results | 1 result, both tier='l2' and tier=None |
| L2 fix preserves regression | — | testing/test_memory.py 5/5 still PASS, T-017 contract 3/3 still PASS |

## Modified

| Path | Change |
|---|---|
| [tools/tools_memory.py](../tools/tools_memory.py) | Two edits — docstring rewrite for `memory_read` (T-017); two-query merge for L2 content search (T-021) |
| [testing/test_memory_roundtrip.py](../testing/test_memory_roundtrip.py) | new — Phase 3 canary (costly, ~$0.05/run) |
| [testing/test_memory_tier_contract.py](../testing/test_memory_tier_contract.py) | new — T-017 reproduction + docstring contract test (free) |
| [testing/test_session_id_propagation.py](../testing/test_session_id_propagation.py) | new — 3 tests, free (no API calls) |
| [testing/test_l2_content_search.py](../testing/test_l2_content_search.py) | new — SM-003 / T-021 reproduction + regression test (free; Supabase only) |
| [FINDINGS.md](../FINDINGS.md) | new — F-001 through F-004 |
| [solutions/SOLUTIONS.jsonl](../solutions/SOLUTIONS.jsonl) | appended S-013 |
| [solutions/LESSONS.md](../solutions/LESSONS.md) | appended L-012 |
| [tickets/closed/T-017-...](../tickets/closed/T-017-memory-read-tier-none-excludes-l1.json) | new (closed) |
| [tickets/closed/T-021-...](../tickets/closed/T-021-l2-content-search-title-only.json) | new (closed) |
| [tickets/open/T-022-...](../tickets/open/T-022-windows-stdout-encoding.json) | new (open) |
| [tickets/open/T-023-...](../tickets/open/T-023-roundtrip-canary-bypasses-memory-read-pathway.json) | new (open) |

`pi_agent.py`, `evolution.py`, `app/`, `core/`, `prompts/`, `SUPABASE_SETUP.sql` — untouched. No runtime behaviour change beyond the two `tools/tools_memory.py:memory_read` edits.

## Findings deferred to later phases

| Finding | Phase to address | Notes |
|---|---|---|
| F-001 → T-023 | Phase 5 (prompt engineering) or its own targeted test phase | Round-trip canary needs to force the memory_read pathway. Test design fix, not runtime. |
| F-002 → T-022 | Future cleanup pass | Windows stdout encoding — replace ✓/box chars with ASCII or set `sys.stdout.reconfigure`. |
| F-003 (memory_read 91.7% success rate) | Phase 5 | Each failed `memory_read` interaction needs review/categorisation. |
| F-004 (truncation boundary not exercised) | Phase 6 | Mocked-Claude test that drives 22+ tool rounds and asserts no orphaned tool_result. |
| T-018 (Anthropic SDK pydantic import hang) | Untouched | P3, not in Phase 3 scope. Stays open. |
| T-019 (normie hallucination) | Phase 5 | Prompt-engineering pass on consciousness.txt. |

## What this phase did NOT close (per Ash's reproduction rule)

- **T-019** (normie tool-mime hallucination): not reproduced this phase, not fixed. Phase 5.
- **T-018** (pydantic import hang): not reproduced, not actionable.
- **F-001 / T-023**: the production failure mode the analysis pipeline keeps documenting (Claude's `memory_read` query formulation missing) is *still unverified by an automated test*. The L2 content search fix (T-021) makes the underlying *storage layer* match queries against full content, but it does NOT prove that Claude's natural-language query formulation hits the right keywords. That's the gap T-023 owns.
- The `memory_read` 91.7% success rate observed in the production log (F-003) — symptom of the gap, not closure.

## Acceptance gate (master prompt §6 Phase 3)

> Ash sees the round-trip test pass, and reads `FINDINGS.md` for any deferred items. He says "phase 4".

Round-trip test passes (with the documented caveat). FINDINGS.md is in place. Awaiting Ash's "phase 4" call to begin the modular refactor of `pi_agent.py`.

## Notes to self

- The reproduction-or-not-resolved rule shaped Phase 3 substantially. Every fix had a pre-fix failure on record (T-017 contract test failing 2/3, L2 content test failing 0 results) before any code changed. That discipline is portable to Phase 4: don't claim the refactor is "behaviour-identical" until the golden tests prove it.
- The PowerShell shell switch mid-phase cost some time on the first attempt to append S-013 (embedded apostrophes in lessons array). The lesson is structural: anything with embedded quotes goes in a temp `_tmp_*.py` script file, run via `python`, then `Remove-Item` after. Avoid inline `python -c` for non-trivial JSON.
- L-012 captures the most important Phase 3 insight — that GREEN canaries can be GREEN for the wrong reason. T-023 owns the follow-up; if Phase 5 doesn't naturally close it, it should get a dedicated test phase before Phase 6.
- Phase 4's golden tests should specifically include a behaviour test that calls `_get_system_prompt()` with a known L3 marker and asserts it appears verbatim in the output — that's the path Phase 3's canary actually exercised, and the refactor must not break it.
