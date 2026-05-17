# ADR-003 — LLMRouter tier matrix + TPD-budget-aware brownout

**Status:** Proposed (awaiting Ash sign-off)
**Date:** 2026-05-17
**Ticket:** [T-084](../../tickets/open/T-084-r3-router-tier-and-tpd-budget.json) (R3 of Hardening Track)
**Author:** Claude (Opus 4.7)

---

## Decision

`LLMRouter.chat()` accepts `tier ∈ {private, premium, balanced, cheap, fast}`.
Each tier maps to an ordered provider preference list. The cost tracker grows
a `tokens_today(provider)` query, and `LLMRouter._is_browned_out()` returns
true when daily token usage exceeds 90% of a per-provider configured budget —
*before* the call fails. All LLM calls in the project flow through
`LLMRouter.chat(tier=...)`; the direct `self.cerebras` client in
`pi_agent.py` is removed; `_respond_normie` and `distill_session` switch to
the router with `tier='cheap'`.

Concretely:

- **Tier preference lists** (declared in `core/llm_router.py`):
  ```
  private:   groq      → ollama                                (god mode, ADR-001)
  premium:   anthropic → gemini                                (paid quality, code edits, tool planning fallback)
  balanced:  anthropic → groq → gemini → cerebras → openrouter (current default — preserved)
  cheap:     cerebras  → groq → gemini → openrouter            (free tiers first, Claude excluded)
  fast:      cerebras  → groq                                  (low-latency hot path, no Claude)
  ```
- **`default` retained as an alias for `balanced`** so any call site that
  didn't pass `tier=` continues to work unchanged. Removed in a followup
  cycle once all sites declare their tier.
- **`tokens_today(provider)`** on `CostTracker`: SQL aggregate
  `SUM(tokens_in + tokens_out) WHERE provider = ? AND date(ts) = date('now')`.
- **`PROVIDER_DAILY_TOKEN_BUDGET`** dict in `llm_router.py` with sane
  defaults matching free-tier published limits (`groq=100_000`,
  `cerebras=1_000_000`, `gemini=1_000_000`, `openrouter=50_000`,
  `anthropic=None`, `ollama=None`). `None` = no budget, never TPD-browned.
  Override via env var per provider (`GROQ_DAILY_TOKEN_BUDGET`, etc).
- **`_is_browned_out(name)`** gains a TPD check that runs first: if
  `tokens_today(name) / budget > 0.9`, return true and tag the brownout
  reason as `"tpd_budget"` so logs distinguish quota brownout from
  hard-failure brownout.
- **Schema migration** on `llm_costs`: `ALTER TABLE llm_costs ADD COLUMN
  tier TEXT DEFAULT 'balanced'` (idempotent PRAGMA check). `CostTracker.record()`
  signature gains `tier` kwarg. Cost dashboard splits by tier.

## Context

Today `core/llm_router.py` routes by availability only:
- Brownout = 5-minute cooldown after a hard failure on any provider.
- First-healthy from the provider list (anthropic → groq → gemini →
  cerebras → openrouter → ollama).
- No cost awareness. No task-fit routing. No TPD-budget guard.

Three concrete problems:

**1. Cerebras was promoted (S-053) but never integrated.** Normie mode was
fixed to use Cerebras as primary in `_respond_normie`, but the integration
is a *direct* `OpenAI(api_key=CEREBRAS_API_KEY, base_url=...)` client at
`pi_agent.py:140-150`, with hand-rolled Cerebras→Groq failover at
`_respond_normie:1116-1155`. The CerebrasProvider class (matching the
GroqProvider/AnthropicProvider shape) was added in T-082 step 5 — but the
normie path still bypasses LLMRouter entirely. **Two failover code paths
that should be one.** S-053's `better_future_fix` calls this gap out
explicitly.

**2. Cost grows unbounded in Phase 7 autonomy.** Pi makes ~10x more LLM
calls during autonomous sprint runs than during interactive chat — mostly
to Claude by default (the first provider in the list). Distillation,
briefing aggregation, normie chat all hit the most expensive provider when
a cheap one would do. The S-058 cost model assumed Pi's Phase 6 call
volume; Phase 7 autonomy already exceeds that.

**3. TPD-limit hits cause silent failures.** Observed during T-077 work:
Groq free tier exhausted at ~100k tokens/day. The call fails with a 429,
LLMRouter marks Groq browned out for 5 minutes, then *tries Groq again*
because brownout TTL elapsed. The pattern repeats every 5 minutes until
the daily quota resets at midnight. A preemptive TPD check ("if today's
usage > 90% of daily limit, skip this provider") would route around the
limit before the failure.

## The contract

### Tier semantics

Tier is a **routing hint about cost/quality tradeoff**, not a strict
contract. The router always falls through to lower-cost providers on
failure, and TPD-brownout never strands a call — the next preference in the
*tier list* is tried first, then if exhausted the full provider list is
the safety net.

| Tier | Meaning | Default for |
|---|---|---|
| `private` | Local-first; cloud calls stay on Ash's account or local Ollama | god mode |
| `premium` | Quality matters more than cost. Claude primary | root mode code edits, tool planning when Claude is desired |
| `balanced` | Current default — Claude first, fallbacks broad | root mode (general) |
| `cheap` | Free-tier providers first, Claude excluded | normie chat, distillation, briefing aggregation, history compression |
| `fast` | Low-latency; Cerebras-first, no Claude | voice mode, interactive normie |

### TPD brownout

```python
def _is_browned_out(self, name: str) -> bool:
    # Hard-failure brownout (existing — 5-min cooldown)
    if time.time() - self._brownout.get(name, 0) < BROWNOUT_SECS:
        return True
    # T-084: TPD brownout (preemptive)
    budget = PROVIDER_DAILY_TOKEN_BUDGET.get(name)
    if budget is not None and self._cost is not None:
        used = self._cost.tokens_today(name)
        if used / budget > 0.9:
            log.info(f"[LLMRouter] {name} TPD brownout: {used}/{budget}")
            return True
    return False
```

When all providers in a tier are browned out (hard + TPD), the router falls
through to the *full* provider list before raising — same safety net the
current code has.

### Cost dashboard

`CostTracker.summary(hours=24)` already returns provider × model × cost.
Adding `tier` to the schema lets the dashboard split costs by intended
use-case (e.g. "normie chat cost this week: $0.04 on cheap tier, $0 on
balanced tier"). Useful for the eventual "is autonomous sprint cost
sustainable" question.

## Alternatives considered

### A1 — Don't add tiers; keep balanced-only

**Pros:** Smallest diff. Routing logic stays simple.
**Cons:** Cost grows unbounded; normie still bypasses router; the cost-tier
split for the audit dashboard never materializes. **Rejected** — the
explicit goal is to make Pi cost-aware.

### A2 — Per-call provider override instead of tiers

```python
router.chat(messages, provider="groq")
```

**Pros:** No abstraction. Call sites pick exact provider.
**Cons:** Call sites become brittle to provider-name changes; loses the
"falling-back chain" win that the existing router provides. Tier hides the
ordering decision in one place (`llm_router.py`); explicit provider names
spread it across every call site. **Rejected** — abstraction is the point.

### A3 — Token-budget-aware *retry* instead of preemptive brownout

After a 429, mark the provider browned-out *until quota resets*. Current
behavior + smarter cooldown.

**Pros:** Reactive — only fires after a real failure. No estimation error
on budget.
**Cons:** Wastes one call per day per provider hitting the limit; that
call is a *failed* call that the user sees as latency. Preemptive brownout
avoids the wasted call. **Rejected** — preemptive is strictly better when
the budget is known.

### A4 — Move tier decision into call sites with no router knowledge

Each call site (normie, distill, briefing) calls a specific provider
directly.

**Pros:** Maximum explicitness.
**Cons:** Same as A2 — spreads routing logic. Plus loses failover. **Rejected.**

## Consequences

### Positive

- One failover code path. `self.cerebras` direct client removed. Normie
  hand-rolled failover at `_respond_normie:1117-1155` collapses into one
  `router.chat(..., tier='cheap')` call.
- Cost-aware routing — `cheap` tier means free-tier providers serve normie,
  distillation, briefing; Claude only fires for actual reasoning.
- TPD brownout prevents the "fail then 5-min cooldown then fail again"
  loop at quota saturation. Daily 100k-token Groq limit becomes a routing
  cue, not a failure mode.
- Cost dashboard split by tier surfaces "what is autonomous sprint
  actually costing" cleanly.
- R10 (T-091) prompt-cache work is unblocked — it needed the tier param
  to choose which calls get cached aggressively (premium yes, cheap no).

### Negative / risks

- **Schema migration on `llm_costs`.** One ALTER TABLE adding `tier`
  column with `DEFAULT 'balanced'`. Idempotent PRAGMA check. Old rows
  show as "balanced" which is accurate (that was the implicit default).
- **Budget defaults may be wrong.** Free-tier published limits change.
  Mitigation: env-var override (`GROQ_DAILY_TOKEN_BUDGET=...`) for each
  provider; defaults documented as "matches commonly-published free-tier
  limits, override if your plan differs."
- **`tier='default'` alias is dead weight after migration.** Listed in
  the docstring as deprecated; followup small ticket to remove it once
  every call site declares an explicit tier.
- **Cerebras requires `cerebras_key` in `.env`.** Already documented;
  graceful degrade exists (LLMRouter skips Cerebras provider if no key).
  No new dependency.
- **TPD brownout depends on CostTracker.** If CostTracker is unavailable
  (e.g. `data/llm_cost.db` locked), TPD check returns false (no
  brownout) — fail-open, never strand a call.

### Neutral

- `LLMRouter.chat(tier=...)` was added in T-082 step 5 for `tier='private'`.
  This expands the matrix; the kwarg shape is unchanged.
- CerebrasProvider already exists from T-082; just needs to be the
  preferred provider for `cheap`/`fast` tiers.

## Migration plan (one step = one commit)

1. **This ADR.** Sign-off before any code.
2. (Already done in T-082 step 5) `CerebrasProvider` matches the provider
   interface; registered with LLMRouter via `cerebras_key` in `__init__`.
   No work here; ADR notes the dependency.
3. Remove `self.cerebras` direct client from `pi_agent.py` `__init__`
   (lines 140-150). Verify pi_agent imports clean; no other call sites
   reference `self.cerebras` yet (only `_respond_normie` does, and that's
   step 5).
4. Extend `_providers_for_tier()` in `core/llm_router.py` with the
   four-tier matrix (private already exists; add premium, balanced,
   cheap, fast). `tier='default'` aliases to `balanced`. Tier list
   constants near the top of the file. Smoke test: tier='cheap' returns
   `[cerebras, groq, gemini, openrouter]` from a fully-populated router.
5. Migrate `_respond_normie` in `pi_agent.py` to call
   `self.router.chat(..., tier='cheap')`. Remove the hand-rolled
   Cerebras→Groq fallback (37 lines). Preserve rate-limit user-facing
   message logic — wrap it around the router call.
6. Migrate `memory/pipeline.py::distill_session` to call the router with
   `tier='cheap'` instead of directly using `groq_client + anthropic_client`.
   Preserve the 3-tier fallback chain semantics (Groq → Haiku → regex)
   via tier ordering + final-resort heuristic.
7. Add `CostTracker.tokens_today(provider)` returning today's
   `tokens_in + tokens_out` SUM for that provider. Schema migration:
   `ALTER TABLE llm_costs ADD COLUMN tier TEXT DEFAULT 'balanced'`
   (idempotent). `CostTracker.record()` gains `tier` kwarg, threaded
   through `LLMRouter.chat()`.
8. Add TPD-budget check inside `LLMRouter._is_browned_out()` per the
   `private/budget/used > 0.9` formula. Add `PROVIDER_DAILY_TOKEN_BUDGET`
   dict + env-var override loader.
9. Add `tests/test_router_tier_and_tpd.py` with: (a) tier matrix
   returns correct ordering per tier; (b) TPD saturation routes to next
   provider in tier; (c) `tier='default'` alias works; (d) cost record
   stores tier; (e) tokens_today() correctly aggregates.
10. Append `SOLUTIONS.jsonl` (S-063 or next). Note that S-053's
    `better_future_fix` is now satisfied. Move T-084 to closed.

## Open questions / follow-ups

- **`tier='default'` deprecation.** This ADR keeps it as an alias for
  one release. Followup ticket removes it once `git grep tier=` shows
  every call site declared. Track as a small T-NNN.
- **Tier-aware prompt cache (R10).** Once R3 ships, R10 can wire
  `tier='premium'` calls to the cached-prompt path more aggressively
  (Claude's prompt cache rewards repeated system prompts). Out of R3
  scope.
- **Per-call-site default tier matrix in CLAUDE.md / PI.md.** A small
  table mapping "the 5 places we make LLM calls" → "which tier each
  one uses" goes in PI.md §6 so future readers don't have to grep.

## Sign-off

- [ ] Ash — read and agree before step 3 begins.
