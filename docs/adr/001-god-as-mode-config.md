# ADR-001 — God mode collapses to a `ModeConfig` instance

**Status:** Proposed (awaiting Ash sign-off)
**Date:** 2026-05-15
**Ticket:** [T-082](../../tickets/open/T-082-r1-god-mode-collapse.json) (R1 of Hardening Track)
**Author:** Claude (Opus 4.7), session 2026-05-15

---

## Decision

God mode stops being a parallel agent (`agent/god.py`, 633 lines) and becomes a
single `ModeConfig` instance registered alongside `root` and `normie` in
`agent/modes.py`. The main response path (`pi_agent._respond`) consumes that
config. Privacy is preserved by `.gitignore` + config-driven file paths, not by
a forked code tree.

Concretely:

- New `ModeConfig` dataclass in `agent/modes.py`:
  `name, prompt_path, memory_db, memory_namespace, vault_path, tickets_dir,
  router_tier, supports_tools, tool_allowlist, max_tokens`.
- `MemoryTools.__init__` gains `db_path` and `namespace` params; god mode
  instantiates a private `MemoryTools(db_path=data/god_memory.db,
  namespace='god', supabase_url=None)` — local-SQLite-only, never reaches
  Supabase.
- `LLMRouter.chat` gains a `tier` param. `tier='private'` restricts the
  rotation to Groq → Ollama (matching `agent/god.py`'s current ordering),
  excluding Anthropic/Gemini/Cerebras/OpenRouter. This is behavioral
  **parity** with the shipping god mode, not a tightening. Inverting to
  Ollama-first or stripping cloud entirely is a separate policy decision
  filed as its own ticket if needed.
- `pi_agent._respond_god` deletes. `_respond` reads the active `ModeConfig`
  and routes accordingly.
- `agent/god.py` archives to `docs/_archive/agent_god_v1.py`. Not deleted (P7
  + Ash rule 2).

## Context

God mode was added as a private, uncensored layer for Ash. The implementation
shortcut was to fork — own SQLite schema, own ticket system, own LLM call
layer (`_groq_call` / `_ollama_call`), own tool registry (20 hand-written
tools), own tool-call format (regex-parsed ` ```tool ` JSON fences instead of
Anthropic tool-use blocks). No call into `LLMRouter`, no call into
`agent/tools.execute_tool`, no use of `MemoryTools`.

This worked at write-time and now bleeds:

- **S-053 (Cerebras as normie primary)** — god mode can't use Cerebras. To
  get it there, you'd duplicate the failover logic a second time inside
  `agent/god.py`. (This is literally the S-053 anti-pattern called out in
  `pi_architecture.md` §5.)
- **T-078 / S-054 (temporal validity windows)** — `god_memory` table has no
  `invalid_at` column. Superseded facts are silently lost in god mode.
- **53 newer tools** — gmail, calendar, scholar, watchers, computer-use,
  obsidian, browser_auto — invisible to god mode. User expectation is that
  Pi capabilities transfer when they say "god mode"; they don't.
- **Drift is guaranteed by construction.** Every improvement to the main
  agent silently does not apply. This is the deviation signal P4 calls out:
  "a subsystem gaining a `god_*` variant of an existing method."

R1 is the first R-ticket because it's blocking R8 (`ModeConfig` dataclass for
all response paths) and because every additional improvement landed without
fixing R1 makes R1 strictly more expensive to land.

## Privacy invariants preserved

The R1 lesson is P7: **privacy is `.gitignore` + config, never code
duplication.** These invariants must remain true after migration; tests in
step 8 will assert each.

| # | Invariant | Mechanism after migration |
|---|-----------|---------------------------|
| 1 | `data/god_memory.db` never enters git | Covered by `data/` in `.gitignore`; `ModeConfig.memory_db` is the only place this path is referenced |
| 2 | `prompts/god_consciousness.txt` never enters git | Covered by `prompts/` in `.gitignore`; `ModeConfig.prompt_path` is the only place this path is referenced |
| 3 | `tickets/god/` never enters git | Listed in `.git/info/exclude` (local-only, never committed; matches commit 41e37f2 pattern of keeping god-mode paths out of the public `.gitignore`); `ModeConfig.tickets_dir` is the only place this path is referenced |
| 4 | `vault/.god/` never enters git | Listed in `.git/info/exclude` (same rationale as row 3); `ModeConfig.vault_path` is the only place this path is referenced |
| 5 | God memory never writes to Supabase | `MemoryTools(supabase_url=None, namespace='god')` short-circuits all Supabase paths; tested in `tests/test_god_memory_isolation.py` |
| 6 | God-mode L1/L2/L3 distillation never reads from public memory | Namespace key partitions queries; `_sync_l3()` skipped when `namespace != 'pi'` |
| 7 | God-mode turns never land in `logs/turns.jsonl` (public) or `raw_wiki` (Supabase) | Logging path gated on `mode_config.public_logging`; default `False` for god |

Rows 1–4 are asserted by `testing/test_god_uses_unified_path.py::test_private_paths_gitignored`,
which parses BOTH `.gitignore` (tracked, public) and `.git/info/exclude`
(local-only) and applies transitive coverage (so `data/` covers
`data/god_memory.db`). Step 8 of the migration plan adds this test.

## Alternatives considered

### A1 — Leave god mode as a fork (do nothing)

**Pros:** Zero migration risk. Privacy is structurally trivial.
**Cons:** Drift compounds. Every R-ticket lands twice or only in main agent.
Pi capability boast does not match god-mode reality. R8 cannot land. This is
the path that produced the 633-line debt in the first place — staying on it
adds more.
**Rejected:** P4 deviation signal "subsystem gaining a `god_*` variant" is
already firing; this option codifies it.

### A2 — ModeConfig dataclass but keep `agent/god.py` for the dispatch loop

**Pros:** Smallest diff. Keeps the regex `tool` parser intact (Groq/Ollama
don't speak Anthropic tool-use blocks natively).
**Cons:** Still two response paths. Doesn't unblock R8. Doesn't deliver the
"≥50 lines off `pi_agent.py`, net reduction ~550 lines" success criterion.
**Rejected:** Half-refactor; P1 says "one refactor at a time, fully
completed."

### A3 — Full collapse including tool-call format unification (this ADR)

**Pros:** Unifies all 73 tools. Inherits LLMRouter failover, cost tracking,
brownout. Memory pipeline improvements (incl. S-054 invalid_at, T-082 access
counters) apply for free.
**Cons:** Tool-call format mismatch is real — Groq supports
OpenAI-style function-calling and `GroqProvider` already translates;
Ollama support varies by model. Mitigation: keep a regex-parsed `tool` JSON
fallback inside the appropriate provider (NOT in pi_agent), gated on
`provider.supports_native_tools`. The fallback parser is the only god-mode
code that survives — and it lives where it belongs (provider layer), not in
a forked agent.
**Selected.**

### A4 — Namespaced public memory (one DB, namespace column)

Considered for memory. **Rejected:** would put god-mode rows in the same
SQLite file as public rows, which means a `cat data/pi.db | strings` leak
exposes them. Privacy by separation of files is stronger than privacy by a
WHERE clause. Two DB files with the same schema is the right shape.

## Consequences

### Positive

- `pi_agent.py` shrinks by ~50 lines (the `_respond_god` method + the `try:
  from agent.god import GodMode` block).
- Codebase net reduction ~550 lines (633 god.py archived, ~80 added across
  modes.py + llm_router.py + tools_memory.py + tests).
- One response path. One LLM router. One memory layer. One tool dispatcher.
- New tools work in god mode the moment they're registered.
- R8 unblocks immediately on close.
- `verify.py` gains a privacy-invariant check (step 8) that fails CI if
  any of the 4 gitignored paths is accidentally tracked.

### Negative / risks

- **Schema migration.** Existing `god_memory.db` has a different schema
  (`god_memory` table with `category, importance, tags, access_count`) vs
  the unified `l3_cache` schema. Mitigation: step 4 of the migration plan
  writes a one-shot `scripts/migrate_god_memory.py` that reads
  `god_memory.db::god_memory` and inserts into a new
  `god_memory.db::l3_cache` with category mapping (`mission|intel|...` →
  L3 entries with appropriate `importance`). Original table renamed to
  `god_memory_v1_backup` rather than dropped.
- **Tool surface explosion.** God mode through unified dispatch can now
  reach `gmail_send`, `telegram_send`, `web_browse`, `computer_*` —
  side-effectful tools that have public-mode-only assumptions
  (Telegram message goes to Ash's public chat ID, gmail_send goes from
  his real account). Mitigation: `ModeConfig.tool_allowlist: list[str] |
  None`. `None` = all tools; for god mode we start with a conservative
  allowlist matching the current 20 god tools and expand explicitly per
  user request. This is the safest default and reversible.
- **Tool-call format fallback.** Groq via `GroqProvider` already translates
  native tool-use; Ollama needs the regex-fence parser preserved. Lives in
  `core/providers/ollama.py` (new file, ~40 lines) — the only piece of
  `agent/god.py` that survives, relocated to the provider layer where it
  belongs.
- **Feature-flag rollback window.** Step 7 keeps `_respond_god` callable
  for one commit while the new path proves itself; step 9 deletes it.
  Worst-case rollback: `git revert` the 2 commits 7–9 to restore the fork
  path; data is untouched because the migration is read-once-rewrite-once.

### Neutral

- The `agent/god.py` archive is preserved in `docs/_archive/agent_god_v1.py`
  per Ash rule 2 (never delete). Re-importable if needed, but not imported
  by anything once R1 lands.

## Migration plan (mirrors T-082, one step = one commit)

1. **This ADR.** (you are here)
2. Add `ModeConfig` dataclass + registry to `agent/modes.py`. Register
   `root`, `normie`, `god`. No call sites changed.
3. Extend `MemoryTools.__init__` to accept `db_path` and `namespace`.
   Skip Supabase when `namespace != 'pi'` or `supabase_url` is falsy.
   Idempotent migration runs on the new DB.
4. Write `scripts/migrate_god_memory.py` — one-shot conversion of legacy
   `god_memory` rows into `l3_cache` rows in the same DB file. Renames
   the old table to `god_memory_v1_backup`. Idempotent; safe to re-run.
5. Extend `LLMRouter.chat` with a `tier` kwarg. `tier='private'` reorders
   providers Ollama-first. Add `core/providers/ollama.py` with the
   regex-fence parser lifted from `agent/god.py`.
6. Build the unified `_respond` in `pi_agent.py` that consumes a
   `ModeConfig`. Initially shadow — called only by new tests.
7. Switch `mode == 'god'` dispatch to the unified `_respond`. Keep
   `_respond_god` in place as fallback (feature-flag: env var
   `PI_GOD_LEGACY=1` restores it). Run god-mode smoke tests.
8. Add `tests/test_god_uses_unified_path.py` asserting:
   - god mode flows through `LLMRouter`
   - god mode flows through `agent/tools.execute_tool`
   - `invalid_at` works in `god_memory.db`
   - the 4 private paths are excluded from git (parses `.gitignore` AND `.git/info/exclude`, honors transitive coverage)
   - god memory never appears in Supabase namespace queries
9. Remove `_respond_god` and the `from agent.god import GodMode` block
   from `pi_agent.py`. `mv agent/god.py docs/_archive/agent_god_v1.py`.
   Drop the legacy feature flag.
10. Append `solutions/SOLUTIONS.jsonl` (id=S-059 next) with
    `root_cause, fix, files_changed, tests, better_future_fix
    (e.g. "private memory deserves its own L1/L2 distillation policy
    rather than reusing public l3_cache distillation cadence"),
    not_done (Phase 9 Telegram peer probably wants per-surface mode
    config, deferred)`.

Each step must end with `python scripts/verify.py` PASS.

## Sign-off

- [ ] Ash — read and agree before step 2 begins.
