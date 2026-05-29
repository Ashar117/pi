# Pi Engineering Layout — Hardening Track

**Status:** Interphase work between Phase 8 (Voice) and Phase 9 (Distributed).
**Blocks Phase 9** because god-mode-as-parallel-agent and 1681-line tool dispatch
cannot survive the addition of Telegram/Discord/web surfaces without quadratic
maintenance cost.

**Goal:** Convert Pi from a working-but-vibe-coded daily-driver into a
deliberately engineered system whose architecture carries it through Phase 9+
without rotting.

**Non-goal:** New user-facing features. Hardening is structural. Feature work
resumes Phase 9 Distributed once R1–R8 close.

**Estimated duration:** 6–10 weeks part-time (one R-ticket per week + buffer).

**Success metric:** R1–R8 closed. R9, R10 closed or explicitly deferred. Net
codebase line reduction ≥ 1,500. Tool count ≤ 45. Planner accuracy improves on
a benchmark of 50 representative prompts.

---

## Workstream map

```
WS-A: Mode & dispatch unification
  R1 (god → mode-config)
       ↓
  R8 (ModeConfig dataclass for response paths)
       ↓
  R5 (sprint × god isolation, validated after R1)

WS-B: Tool architecture
  R2.1 (registry pattern)
       ↓
  R2.2 (tool consolidation 73 → ~40)
       ↓
  R2.3 (weekly auto-prune script)

WS-C: Routing & cost
  R3 (LLMRouter tier param + Cerebras integration + TPD budget)
       ↓
  R10 (L3 prompt-cache segment, depends on R3 done)

WS-D: Reliability
  R4 (resumable exit)
  R9 (dropped-log local fallback) — independent
  R6 (partition recovery) — pre-work only; full impl deferred

WS-E: Cleanup
  R7 (archive SelfModifier) — afternoon job, no deps
```

Workstreams can run in parallel if multiple sessions are dedicated. Realistically:
serial, one R per week.

---

## R-ticket index

Full specs live in `tickets/open/T-NNN-rN-*.json`. Summary:

| R# | T# | Title | Sev | Effort | Depends on | Blocks |
|----|----|-------|-----|--------|------------|--------|
| R1 | T-082 | God mode collapse → mode-config | P0 | 5-7d | — | R8 |
| R2 | T-083 | Tool registry + consolidation + audit | P0 | 1.5-2w | — | — |
| R3 | T-084 | Router tier + TPD budget | P1 | 3d | — | R10 |
| R4 | T-085 | Resumable session exit | P1 | 5-7d | — | — |
| R5 | T-086 | Sprint × god isolation | P1 | 2-4h | — | — |
| R6 | T-087 | Partition recovery pre-work | P3 | 30m | — | — |
| R7 | T-088 | Archive SelfModifier | P3 | 1h | — | — |
| R8 | T-089 | ModeConfig dataclass | P2 | 3d | R1 | — |
| R9 | T-090 | Dropped-log local fallback | P2 | 4h | — | — |
| R10 | T-091 | L3 prompt-cache segment | P3 | 1d | R3 | — |

---

## Recommended week-by-week

**Week 1:** R1 (god collapse) — start here. ADR first.
**Week 2:** R2.1 (registry pattern only — no merges yet)
**Week 3:** R2.2 + R2.3 (tool consolidation + audit cron)
**Week 4:** R3 (router tier + TPD)
**Week 5:** R8 (ModeConfig — unlocked by R1 closing)
**Week 6:** R5 + R7 + R9 (cleanup batch — small tickets bundled)
**Week 7:** R4 part 1 (resumable exit infra)
**Week 8:** R4 part 2 (move ops out of exit) + R10 (L3 cache)
**Week 9:** R6 pre-work + buffer/spillover
**Week 10:** Retro + Phase 9 Distributed kickoff

---

## Verification matrix

Every R-ticket merge requires:

| Gate | Requirement |
|------|-------------|
| `python scripts/verify.py` | PASS |
| Smoke test | Tool/mode/feature being changed still callable end-to-end |
| Solution entry | `solutions/SOLUTIONS.jsonl` appended with `root_cause`, `fix`, `tests`, `not_done`, `better_future_fix` |
| ADR (if listed) | `docs/adr/NNN-*.md` written before merge |
| Line-count delta | Recorded in solution entry — refactors should show net reduction or near-zero |
| PI.md refresh | `python scripts/refresh_pi.py` after ticket close |

---

## Definition of done for Hardening Track

- [ ] R1, R2 (.1/.2/.3), R3, R4, R5, R7, R8, R9, R10 closed
- [ ] R6 pre-work landed; full implementation deferred until incident
- [ ] Net codebase line reduction ≥ 1,500
- [ ] Tool count ≤ 45
- [ ] All response paths unified through one `_respond` method
- [ ] All LLM calls route through `LLMRouter`
- [ ] Session exit ≤ 3 ops, resumable
- [ ] 5 ADRs written under `docs/adr/`
- [ ] `prompts/pi_architecture.md` loaded into root-mode via `{{INCLUDE:pi_architecture.md}}`

When all boxes check: open `tickets/open/T-NNN-phase-9-kickoff.json` and resume
feature work on the now-clean architecture.

---

## How to read this with `prompts/pi_architecture.md`

These two files are paired:

- **`prompts/pi_architecture.md`** — loaded by Claude every session. Defines
  *what Pi is* and *the principles that prevent deviation*. Read by the LLM
  before any code change.

- **`docs/PI_ENGINEERING_LAYOUT.md`** (this file) — the work plan. Read by
  Ash + Claude at session start when deciding *which R-ticket to pick up*.
  Treat it as the active backlog for the Hardening Track.

When a new architectural issue surfaces that's not in R1–R10, file a new
T-ticket, add it to the R-table above as R11+, and update the pi_architecture
"Known architectural debt" table.
