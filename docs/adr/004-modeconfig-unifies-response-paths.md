# ADR-004 — `ModeConfig` absorbs all 3 response paths (root + normie + god)

**Status:** Proposed (awaiting Ash sign-off)
**Date:** 2026-05-17
**Ticket:** [T-089](../../tickets/open/T-089-r8-modeconfig-dataclass.json) (R8 of Hardening Track)
**Author:** Claude (Opus 4.7)

ADR numbering note: this is `004` per the ticket spec; ADR-005 (R4 resumable
exit) already shipped because R4's work was on a faster path.

---

## Decision

Extend `ModeConfig` (already shipped in R1 / ADR-001) with 5 new fields
covering the divergent behaviors between root, normie, and god response
paths. Refactor each `_respond_*` method to consume its `ModeConfig` instead
of hardcoded checks. In a follow-on session, identify the common skeleton
and extract `_respond_via_config` as the single body; delete `_respond_root`
+ `_respond_normie` (`_respond_god` was already deleted in T-082).

**Shipping in 3 stages** per the ticket spec:

- **Stage A — parameterization (this session).** New `ModeConfig` fields +
  `_respond_*` methods read from config; behavior unchanged; verify PASS.
- **Stage B — skeleton extraction (next session).** Identify the common
  prompt-build → router.chat → tool-loop → log shape. Add it to the
  existing `_respond_via_config`.
- **Stage C — dispatch unification (next session).** `process_input` calls
  `_respond_via_config(MODE_CONFIGS[self.mode], ...)` for all three modes.
  Delete `_respond_root` + `_respond_normie`. Target: ≥200 lines off
  `pi_agent.py`.

This ADR locks the contract; staging keeps each commit independently
revertable per P1 ("one refactor at a time, fully completed").

## Context

`pi_agent.py` after R1 has:

- `_respond_root` (~130 lines): Anthropic-tuple system prompt, awareness
  shortcut, memory prefetch, full LLMRouter tool loop, evolution log,
  L1 turn log.
- `_respond_normie` (~80 lines after R3 trimmed it): slim normie consciousness,
  session-ctx injection, LLMRouter `tier='cheap'` single-call, evolution log,
  L1 turn log.
- `_respond_via_config` (~150 lines): god's unified path. ModeConfig-driven.
  Built in R1 step 6 specifically to receive root + normie when R8 lands.

R1 already shipped `ModeConfig` with the per-mode config fields most things
need. R3 unified the LLM call path. R8 is the merge: take the two remaining
methods, run them through the same skeleton.

**Why this matters operationally:** every new feature today threads through
three methods. T-084 (router tier) had to touch all three to migrate
Cerebras off the direct client. T-085 (resumable exit) had to add mid-session
work in `_maybe_mid_session_distill` because there was no shared "exit-of-turn"
hook. The pattern is "structural feature → 3-place edit," which is the
exact vibe-coding-plateau anti-pattern P1 warns against.

## The 5 new `ModeConfig` fields

Mapped from divergent behaviors in the current 3 methods:

| Field | Type | Why | Mode values |
|---|---|---|---|
| `prefetch_memory` | `bool` | Root prefetches L3 memory before each turn ([pi_agent.py:_prefetch_memory](pi_agent.py)). Cheap, root-only today. | root=True, normie=False, god=False |
| `awareness_shortcut` | `bool` | Root tries `try_answer_from_awareness` (cached weather/news) before any LLM call — latency optimization. | root=True, normie=True (already does this), god=False |
| `session_ctx_inject` | `bool` | Normie injects `extract_text_from_messages(n=10)` into the system prompt so Groq sees recent context. Compensates for its smaller context window. | root=False, normie=True, god=False |
| `builds_handoff_on_exit` | `bool` | When leaving this mode for another mode, build a handoff summary via `_build_normie_handoff()` + `_archive_normie_session_to_vault()`. | root=False, normie=True, god=False |
| `consumes_handoff_on_first_turn` | `bool` | On first turn after entering this mode, if `self._normie_handoff_context` is set, prepend it to the system prompt. | root=True, normie=False, god=False |

**Why two handoff fields instead of one:** the handoff mechanism is
asymmetric — normie *builds* when leaving, root *consumes* when entering.
A single `handoff_capable` field collapses that asymmetry and would need
mode-checking elsewhere to decide build-vs-consume. Two clean booleans
keep the build/consume split explicit, which matters when god mode later
participates (god might consume a handoff from root without ever building
one for the next mode).

**Why not also add `anthropic_cache_tuple: bool`:** root's
`build_system_prompt_split` returns `(static, dynamic)` for Anthropic's
prompt cache; the other modes flatten to a string. This is a routing
detail that lives inside `router.chat` — when the chosen provider is
Anthropic, the tuple is honored; otherwise it's flattened by
`system_for_cache = "\n\n".join(system) if isinstance(system, tuple)`.
**No new field needed** — the existing flatten logic handles it.

## Alternatives considered

### A1 — One generic `prepare_prompt` callable per mode

```python
config = ModeConfig(..., prepare_prompt=_root_prepare_prompt)
```

**Pros:** Maximum flexibility. Each mode owns its full prompt-build flow.
**Cons:** Pushes the divergence into 3 separate callables; the structural
win of "feature lands in one place" is lost — instead it lands in
`_root_prepare_prompt` only, and normie/god still get nothing.
**Rejected** — defeats the merge goal.

### A2 — Pure boolean flag matrix (this ADR)

**Pros:** Each behavior is a discrete on/off. New mode = pick the matrix
row. No new code paths.
**Cons:** Flag explosion risk — adding a 6th flag, 7th flag is easy. Need
discipline to keep ModeConfig small.
**Selected** — same shape as R1's `ModeConfig` already uses.

### A3 — Inheritance / strategy pattern (subclasses for RootMode, NormieMode, GodMode)

**Pros:** Classic OO; behavior overrides are explicit.
**Cons:** Three subclasses == three response paths in a different costume.
Doesn't solve the structural problem; just moves it.
**Rejected.**

### A4 — Don't merge; live with 3 methods

**Pros:** Zero risk.
**Cons:** Every new structural feature pays 3× cost in maintenance and
divergence. R8 is on the architecture deviation list because this is
the wrong default.
**Rejected** — the whole Hardening Track exists to fight exactly this.

## Consequences

### Positive (after Stage C)

- One `_respond` method instead of three. New mode = one ModeConfig entry,
  zero new methods.
- New structural features land in `_respond_via_config`; all modes
  benefit immediately. R10 (prompt-cache segment) and any future
  per-turn instrumentation work get this for free.
- ~200 lines off `pi_agent.py` (target from ticket).
- The 5 new ModeConfig fields are auditable per-mode in `MODE_CONFIGS`:
  `grep prefetch_memory agent/modes.py` answers "which modes prefetch?"

### Negative / risks

- **Hot-path refactor.** Every conversation turn goes through this code.
  Mitigation: Stage A is pure parameterization (no behavior change, just
  reads the field from config instead of being hardcoded). Stage A ships
  this session; B and C wait for a follow-on session with focused
  attention.
- **Two-handoff-field design has a subtle asymmetry.** Adding a 4th mode
  needs to think about both `builds_handoff` and `consumes_handoff`.
  Document the asymmetry in `agent/modes.py` so the gotcha is visible.
- **Stage A introduces dormant fields.** After Stage A, the 5 new fields
  are populated but the code still has hardcoded checks (`if self.mode ==
  "root"`). Stage B+C replace the hardcoded checks with `config.X` reads.
  Between A and B+C, the fields are "set but not read in the new way" —
  the only risk is forgetting to do Stages B+C. Mitigation: `T-089`
  ticket stays open with explicit `progress_note: "Stage A done; B+C
  pending"` until done.

### Neutral

- `ModeConfig` is a frozen dataclass; adding 5 fields with defaults is
  non-breaking. Existing god-mode usage (R1's `_respond_via_config`)
  continues unchanged.
- Stage A involves no test changes — same behavior, same outputs. The
  acceptance test will land in Stage C ("all three modes use the same
  code path").

## Migration plan

**Stage A (this session) — ModeConfig parameterization:**

1. **This ADR.** Sign-off.
2. Extend `agent/modes.py::ModeConfig` with the 5 new fields. Update
   `MODE_CONFIGS["root"]`, `["normie"]`, `["god"]` to set them per the
   table above. Default values chosen to preserve current behavior.
3. Refactor `_respond_root` to read `prefetch_memory`,
   `awareness_shortcut`, `consumes_handoff_on_first_turn` from
   `get_mode_config("root")`. Replace hardcoded checks with config reads.
4. Refactor `_respond_normie` to read `session_ctx_inject`,
   `awareness_shortcut`, `builds_handoff_on_exit` from
   `get_mode_config("normie")`. (Handoff-build currently fires from
   `process_input` on mode switch, not from `_respond_normie` — confirm
   placement during code.)
5. `verify.py` PASS; existing tests pass (no behavior change).
6. Commit Stage A. T-089 stays open with `progress_note: "Stage A done"`.

**Stages B + C (follow-on session):**

7. Stage B: identify common skeleton — extract into `_respond_via_config`
   (the existing god method). Each branch (`if config.prefetch_memory:`,
   etc.) replaces the per-method hardcoded version.
8. Stage C: route `process_input` → `_respond_via_config(MODE_CONFIGS[self.mode], ...)`
   for all three modes. Delete `_respond_root`, `_respond_normie`. Add
   `tests/test_response_path_unification.py` asserting all three modes
   share the same dispatch.
9. Append `SOLUTIONS.jsonl` (S-067 or next). Close T-089.

## Open questions

- **Stage A boolean defaults.** Each new field gets a default that
  preserves current behavior. Root's defaults: `prefetch_memory=True`,
  `awareness_shortcut=True`, `consumes_handoff_on_first_turn=True`,
  others False. Normie's: `session_ctx_inject=True`,
  `awareness_shortcut=True`, `builds_handoff_on_exit=True`, others
  False. God's: all False (already wired this way). Default for
  unspecified configs: False (safest).
- **Where does handoff-build fire?** Currently in `process_input` on
  detect of mode-switch from normie → root, *before* `_respond_root`
  runs. Stage A might leave it where it is (process_input does
  `if old_config.builds_handoff_on_exit: ...`). Stage C might pull it
  inside the unified `_respond` body. Decide during Stage B; ADR locks
  the field, not the call site.
- **god → root handoff?** Today god never builds a handoff (its messages
  are private; copying them into a root prompt would defeat the privacy
  invariant). After R8, `MODE_CONFIGS["god"].builds_handoff_on_exit = False`
  enforces this in code. Same for `consumes_handoff_on_first_turn`.

## Sign-off

- [ ] Ash — read and agree before Stage A code begins.
