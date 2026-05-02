# CURRENT — pointer to active checkpoint

**Phase:** 3 — Memory round-trip + T-017 + L2 content search + session_id propagation
**Status:** complete; awaiting Ash's "phase 4" to proceed.
**Active checkpoint:** [phase-3-complete.md](phase-3-complete.md)
**Previous:** [phase-2-complete.md](phase-2-complete.md), [phase-1-complete.md](phase-1-complete.md), [phase-0-complete.md](phase-0-complete.md)
**Last updated:** 2026-04-26

## At-a-glance state

- Round-trip canary GREEN (with caveat: bypassed memory_read tool path via L3 ambient context — see T-023).
- T-017 closed (docstring fix).
- T-021 opened-and-closed (SM-003 L2 content search fix — two-query merge over title + content->>text).
- T-022 opened (Windows stdout encoding, P3).
- T-023 opened (round-trip canary caveat — needs a forcing test design).
- Session_id propagation verified across the real production log (3 multi-turn sessions internally consistent) and via in-process simulation.
- Memory round-trip (storage → restart → retrieval-via-ambient-context) works end-to-end.
- 4 new tests in `testing/`. 3 free (Supabase or in-process only). 1 costly (~$0.05/run).
- 2 audit-trail entries added: S-013 + L-012.
- Runtime files untouched except 2 edits to `tools/tools_memory.py`.

## What this phase did NOT close

Per Ash's reproduction rule, the LOG1/LOG2 production failure mode (Claude's natural-language `memory_read` query formulation missing) **remains unverified by an automated test**. T-023 owns the gap. The L2 content search fix (T-021) makes the *storage layer* match queries against full content, but does not prove Claude's *queries* are well-formed. Phase 5's prompt-engineering pass is the natural follow-up.

## Next step

Ash reads [phase-3-complete.md](phase-3-complete.md), confirms the round-trip canary output and the T-021/T-023 split. Says "phase 4" to begin the `pi_agent.py` modular refactor — purely mechanical, behaviour-preserving, gated by golden tests written first.

Master prompt §6 Phase 3 acceptance gate must close before Phase 4 begins.
