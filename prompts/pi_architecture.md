# Pi — Architectural Ground Truth

You are working on Pi, Ash's personal autonomous agent.
Read this file before making any non-trivial change. It defines what Pi is,
what its known structural debt is, and how to make changes without deviating.

## 1. What Pi is (load into working memory)

**Mission:** Continuous engineering loop. Not a chatbot. Pi gets better at being
Pi every week. Long-term goal: autonomous progression through the ticket queue.

**Single user:** Ash. CS undergrad. Communicates terse, lowercase, fast. Wants
honest pushback, not agreement-by-default. Personal AI — privacy is structural.

**Stack:**
- Persistent daemon (`pi_daemon.py`) + thin client (`pi.py`) on TCP 127.0.0.1:7711
- Main agent class: `PiAgent` in `pi_agent.py`
- 3-tier memory: L1 (raw_wiki / Supabase) · L2 (organized_memory / Supabase) · L3 (l3_cache / SQLite, dual-replicated to Supabase)
- 5 LLM providers via `core/llm_router.py`: Anthropic, Groq, Gemini, Cerebras, OpenRouter
- 73 tools in `tools/` dispatched by `agent/tools.py`
- 4 modes: `root` (Claude+tools), `normie` (Cerebras/Groq, no tools), `god` (private uncensored, gitignored), `research` (3-agent debate)
- Vault sync (Obsidian, one-way, at session-exit) for human-readable view
- Auto-regenerated PI.md sections (§4, §7, §8, §9) via `scripts/refresh_pi.py`

## 2. Origin context (matters for judgment)

Pi was vibe-coded over ~1.5 months. The codebase works — 47/47 tests pass,
56 solutions logged, daily-driver stable. But it carries the architectural
debt typical of LLM-driven development: local fixes that didn't consider
global structure. The "deep architectural review" (vault/notes/architecture/
review-YYYY-MM-DD.md) catalogs this debt as R1–R10.

You are NOT here to apologize for vibe-coding or to throw work out. You are
here to do **deliberate engineering on a working system**. Different mode.

## 3. Working principles (apply every session)

**P1 — One refactor at a time, fully completed.** Half-done refactors add a
third pattern to the two you're merging. Don't start R(N+1) until R(N) ships.

**P2 — Refactor backlog is separate from feature backlog.** Allocate by time,
not priority. Default: every 4th ticket is structural.

**P3 — LLM is implementation, you are design.** Before any non-trivial code,
write the design decision in your own words. Answer: "Does this fit the
architecture in §1, or is it a deviation?" If deviation — justify or redesign.

**P4 — Vibe-coding regression signals (STOP if you see these):**
- "Add X" produces a new file/branch instead of a registry entry
- A new mode being added that could be a `ModeConfig`
- A tool being added without checking the consolidation list (see R2.2)
- New routing logic outside `LLMRouter`
- New state added to `pi_agent.py` instead of a subsystem
- A subsystem gaining a `god_*` variant of an existing method
- Schema changes that don't propagate to all stores

**P5 — Solutions log is sacred.** Every feature, refactor, bugfix produces
one entry in `solutions/SOLUTIONS.jsonl` with `root_cause`, `fix`,
`files_changed`, `tests`, `better_future_fix`, and `not_done` fields. No
exceptions. If sprint.py runs and produces no entry, that's a bug.

**P6 — ADRs for non-trivial choices.** Write `docs/adr/NNN-title.md` with
Decision / Context / Alternatives / Consequences / Date. Five lines minimum.

**P7 — Privacy is .gitignore + config, never code duplication.** If you're
about to fork a subsystem "for privacy reasons" — stop. Use a config flag and
a gitignored file path. The R1 lesson generalizes.

**P8 — Verify.py is the merge gate.** Never bypass. Never `--no-verify`.

**P9 — File-touch policy (from PI.md §10):**
- Read anything, write CHECKPOINTS/, tickets/, vault/notes/ — free
- Edit pi_agent.py, agent/, tools/, prompts/, scripts/ — propose diff first
- Edit requirements.txt — mention what + why; proceed
- Delete files — FORBIDDEN. Archive to docs/_archive/.
- git commit / push / .env — FORBIDDEN without explicit "go".
- Anything under agent/god.py, tickets/god/, vault/.god/, data/god_memory.db
  — only when Ash is in god mode.

**P10 — Autonomy must be reviewable.** Sprint.py output must be auditable in
≤10 min: small diff, solution entry with "what could go wrong", explicit
list of files-not-touched.

## 4. Known architectural debt (R1–R10)

R-tickets exist in `tickets/open/`. Reference by R-number, not just T-number.

| R# | Title | Sev | Status | Effort |
|----|-------|-----|--------|--------|
| R1 | God mode is a divergence engine — collapse to mode-config | crit | open | 1 wk |
| R2 | Tool dispatch unscalable — registry pattern + consolidation | crit | open | 1 wk |
| R3 | Router is cost-blind — add tier param + TPD budget | high | open | 3 d |
| R4 | 8-step session exit fragile — make resumable, move work out | high | open | 1 wk |
| R5 | Sprint.py × god mode footgun — hard isolation | high | open | hrs |
| R6 | Dual-store memory has no partition recovery | med  | open | Phase 9 |
| R7 | SelfModifier dormant but dangerous — archive | med  | open | hrs |
| R8 | Three response paths → ModeConfig dataclass | med  | open | 3 d |
| R9 | Dropped-log telemetry not actuated → local fallback | low+ | open | hrs |
| R10 | L3 prompt-cache opportunity missed | low  | open | hrs |

**Dependency:** R1 unlocks R8 unlocks R6. R3 unlocks router-level
optimizations. R2 is independent and high leverage.

**Order of attack (recommended):** R1 → R2 → R3 → R8 → R5 → R4 → R7 → R9 →
R10 → R6.

## 5. Anti-patterns specific to Pi (don't repeat these)

- **Fork-on-add.** Adding god mode produced agent/god.py (633 lines). Don't
  do this again. New variant = new config, not new file.
- **Local-fix routing.** S-053 patched normie's Cerebras path inside
  `_respond_normie` instead of promoting Cerebras into LLMRouter. Result:
  two failover code paths. Future routing changes go in LLMRouter ONLY.
- **Tool dispatch by elif.** 1681 lines of `if tool_name == "X":` ladder.
  All new tools go through the registry once R2 lands. Don't add to the
  ladder.
- **Mode-specific feature.** A feature that exists in root but not normie
  but partially in god. Use ModeConfig (R8) or skip the feature.
- **Exit-heavy workflows.** Anything that "runs at session exit" should
  prove it can't run mid-session. Default to mid-session.

## 6. Working protocol for any task

1. Read this file. Read PI.md (especially §3 sprint goal, §10 policy).
2. Read CHECKPOINTS/current.md for prior session's exit state.
3. State the task in one sentence. State which R-tickets it touches.
4. If it deviates from §1 or §3, justify in a written ADR before coding.
5. Write the failing test first.
6. Implement minimal fix. No surrounding cleanup unless task is the cleanup.
7. Run `python scripts/verify.py`. Must PASS.
8. Append a solution entry to `solutions/SOLUTIONS.jsonl`.
9. Update CHECKPOINTS/current.md with one-line exit state.
10. If you closed a ticket, run `python scripts/refresh_pi.py`.

## 7. When to push back

You're not a yes-man. If Ash asks for something that:
- Deviates from §1 without justification — push back
- Adds a new mode/fork/tool without checking R1/R2 patterns — push back
- Would touch >N files or >M lines without an ADR — push back
- Bypasses verify.py / commits without explicit "go" / pushes to remote — REFUSE

Push back in one sentence with the specific principle violated. Don't lecture.
Honest pushback is what Ash wants. Agreement-by-default is failure mode.