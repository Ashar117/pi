# CURRENT — pointer to active checkpoint

**Phase:** Hackathon-prep refinement → doc truth pass (fix/conversation-coherence)
**Status:** All hand-docs rewritten against code 2026-07-07 (PI.md, CLAUDE.md, README, ABOUT, ARCHITECTURE v4, USER_GUIDE, CONTRIBUTING, FEATURE_LIST, PI_CONTROL, PROJECT_MAP third pass); 3 completed planning docs archived. Fresh-eyes audit done: 1 gate blind spot fixed, T-276 filed as the genuine sprint.py candidate (T-256 unblocked). verify.py PASS confirmed twice 2026-07-06/07. Branch still NOT landed (T-272) — Ash-gated.
**Active checkpoint:** this file
**Last updated:** 2026-07-07

## Session 2026-07-07 — full doc rewrite + fresh-eyes audit (Fable, final sessions)

**Context:** Ash's ask: rewrite every .md — refine, kill outdated claims, precise workflows, honest architecture, archive the useless — then a fresh-eyes check. Motivation: Fable 5 leaving the Pro plan; judgment must be captured in repo artifacts (docs, gates, tickets), not in whichever model is in the chair.

**Docs:**
- **PI.md** manual sections: §3 current sprint (prove-what-shipped week), §5 loop now shows the real SOLUTIONS schema + never-pipe-verify (T-214) + the ticket-quality bar ("rich tickets = model independence"), §6 adds network layer + corrected counts, §12 roadmap is per-phase honest status (voice = hardware-blocked, sprint = unproven), §13 fire-table no longer tells sessions to edit a dispatch ladder deleted in T-083.
- **docs/ARCHITECTURE.md v4** — clean rewrite; v3 (whose body still described the April system: "eight tools", pre-registry, old ticket schema) archived to `docs/_archive/2026-07-07/`.
- **docs/USER_GUIDE.md** — full rewrite (was: "all 8 tools", references to archived SCHEMA_MISMATCHES docs, "L1 auto-logging not implemented").
- **docs/CONTRIBUTING.md** — real ticket schema at last (was a schema no ticket uses); fixed false claims (`@pytest.mark.costly` doesn't exist — it's the `COSTLY_TESTS` set; tools go in `tools/tools_*.py`, not `integrations/`).
- **PI_CONTROL.md** — killed the hand-copied 63-tool table (it said `gmail_send` "Send an email" — a safety lie post-T-271); now points at auto-generated `prompts/capabilities.md` + lists only code-enforced contracts. Phase history brought to Phase 9.x.
- **README / ABOUT / CLAUDE.md** — counts corrected (21 tool modules, ~75 tools), drift-prone hand-tables replaced with pointers to auto docs, new capability rows (email triage HITL, P1 alerting, CI, silent-failure telemetry), voice claim honesty (◐, hardware-blocked), sprint-runner claim honesty (🟡 until first production close).
- **docs/FEATURE_LIST.md** — statuses trued up; C-011 workflow-builder + O-008 Discord marked rejected (2026-07 audit); I-003 notes SelfModifier was archived T-088; new ✅ rows I-009/O-013/E-013/E-014.
- **docs/PROJECT_MAP.md** — third audit pass appended (state delta, refreshed claim-vs-reality, receipts-needed list).
- **Archived** (docs/_archive/2026-07-07/): UPGRADE_PLAN.md, PI_ENGINEERING_LAYOUT.md, PHASE_8.8_CARETAKER.md — completed/superseded plans; ADRs + tickets carry the durable record.

**Fixes (code):**
- `testing/test_persistence.py:104` — same T-275 encoding crash class, sibling file (was failing).
- `scripts/verify.py` bare-except gate now excludes `docs/_archive/` — the never-delete rule guarantees old-style code lands there; without the exclusion, archiving any file with a bare `except:` would fail CI forever.

**Fresh-eyes audit results:**
- Verified clean: no `messages().send()` outside drafts (T-271 holds); watcher wiring uses real attributes (T-274 holds); `agent/tools.py:319`'s `send_message` import is the module-level function (exists, telegram tools line 132) — false alarm.
- **T-276 filed (open, deliberately unfixed):** six dormant Windows-encoding landmines in testing/ (same class as T-275). Small, verified, testing/-only, reproducing test specified — the genuine sprint.py candidate T-256 was waiting for. T-256's risk_notes now point at it.
- Noted, no ticket: email-watcher seen-ids set trims unordered past 200 ids (self-healing via the `newer_than:1d` query window); `rotate_turns_log` reads 50MB into RAM and has a tiny truncate race at 03:30 idle rotation (acceptable).
- 3 stale-failure ghosts from an 18-min suite run predating the fixes (test_refresh_pi, telegram media routing ×3) — re-run individually: all green.

**Next concrete steps (in order):** 1) T-272 land the branch (Ash's git go). 2) Live email-triage demo run (TELEGRAM_SMOKE.md). 3) `sprint.py --dry-run --ticket T-276`, then `--auto-implement` — first production close (T-256).

## Session 2026-07-06 — hackathon-prep bug sweep + ponytail pass

**Context:** Ash asked for a hackathon-motivated refinement pass: fix bugs, simplify, find what's missing. 22 tickets were filed (T-249..T-271) in a prior planning session; Ash then said "start coding and dont stop till all is done."

**What changed — 20 tickets closed (S-191..S-210):**
- **T-273** (was T-250) — bare `except:` in research_mode.py + permanent verify.py lint against the pattern.
- **T-251** — planning cadence restarted via `plan_sprint.py --auto` (PI.md §3 was 8 weeks stale).
- **T-252** — triaged `analysis/candidate_tickets.jsonl`; fixed a regex bug in `ticket_candidate_miner.py` (matched "HACK" inside "Hacker News", missing `\b`).
- **T-253** — diagnosed 3 cascading passive-skill FAILs (mostly the intentionally-dirty branch, not independent bugs).
- **T-254/T-255** — 5 Telegram + 2 media silent-failure fixes (guest approval notifications, media-to-memory storage, video-gen chain-exhaustion messaging, PDF vision-analysis tracking).
- **T-257/T-258** — new Gmail inbound-triage watcher + Telegram buttons (Draft reply/Add to calendar/Ignore) — the Track1+Track2 hackathon demo flow.
- **T-259** — `logs/turns.jsonl` gzip rotation past 50MB, wired into the daemon scheduler.
- **T-260** — `.github/workflows/verify.yml` (windows-latest, mirrors the dev box).
- **T-261** — real handler-level Telegram tests (fake bot drives actual `_register_handlers()` closures) + manual smoke checklist doc.
- **T-262** — `deep_debate` root-mode tool wrapping the 3-agent research debate (had to add an `interactive=False` flag to `run_research_mode` — it had a blocking `input()` that would've hung forever as a tool call).
- **T-265** — throttled Telegram alerting on P1-class `silent_failures.db` categories.
- **T-266** — memory-pollution detector was scanning `vault/.god/` and leaking its filename into a non-god report — excluded god paths; left the 168-files-missing-frontmatter WARN alone (auto-generated snapshots, not worth mass-editing).
- **T-267** — voice loop: code is complete and well-tested (13 tests), but this environment has no sounddevice/torch/openwakeword installed and no physical mic access — documented as an honest blocker in `docs/VOICE_LOOP_STATUS.md`, not faked.
- **T-268** — Gemini/Imagen backend for `image_gen` (opt-in, pollinations stays default).
- **T-270** (new, found during T-252 triage) — **L3 memory sync silently dropped brand-new writes** once `l3_active_memory` crossed ~1000 rows (Supabase's implicit page cap, no `.order()`/`.limit()`). Reproduced live, fixed with `.order("created_at", desc=True).limit(5000)`. This is the project's #1 recurring bug class (write/read divergence) in a new spot.
- **T-271** (new, found while building T-258) — `gmail_send`'s own docstring promised "draft-only" but the code called `messages().send()` directly — a real, immediate send with zero HITL gate. Fixed to actually create a Gmail draft via `drafts().create()`.
- **T-274** (new, found while wiring T-258) — `pi_agent.py` wired watcher Telegram alerts from `getattr(self.telegram, "send_message", None)` — **that attribute never existed** (only `.send()` does), so watcher-to-Telegram alerts have silently never worked in production for any watcher type, ever. Fixed.
- **T-275** (new, found during final verify) — `test_modes.py` had a Windows-only `UnicodeDecodeError` from `open()` missing `encoding='utf-8'`.

**Mid-session discovery: a concurrent session was live.** Another Claude Code session was independently fixing real Telegram bugs Ash reported live (screenshots — `telegram_react` leaking as text, `/approve`+`/deny` crashing with HTML 400s) and closed 2 tickets under the exact same IDs (T-249/T-250) this session had already used. Reconciled: renumbered this session's T-249/T-250 → T-272/T-273 (JSON `id` field only — tooling reads that, not the filename), verified both sessions' edits to `tools/tools_telegram.py` coexist with no data loss, fixed `test_normie_tools.py` which broke because their fix (allowing `telegram_send`/`image_gen` in normie) made the test's old blocklist assertion stale.

**T-256/T-263 (sprint.py production close) — blocked, not done.** The point was to prove `scripts/sprint.py --auto-implement` can autonomously close a real ticket in production (it never has). But every substantive ticket this session got implemented by hand instead of left for the runner, so the candidate pool is empty. Asked Ash directly; decision: skip for now rather than manufacture an artificial ticket. Revisit when a real small ticket exists in the queue.

**Still open:** T-272 (branch landing, Ash-gated), T-256/T-263 (blocked per above), T-264 (needs weeks of `silent_failures.db` telemetry before it can start), T-269 (intentional piggyback-only policy, not meant to close).

**Next concrete step:** Ash reviews this session's diff, then decides on landing T-272 (the coherence branch) in the chunked, Ash-gated commits its own migration_plan describes.

## Session 2026-05-29 — conversation coherence

**What changed**
- **T-148 (closed, S-088):** `_block_text()` helper in [agent/truncation.py](agent/truncation.py) now captures canonical `{"type":"text"}` assistant dicts that `hasattr(block,"text")` silently dropped. Fixed in both `extract_text_from_messages` (normie session ctx + L2 session summary) and `_build_context` (root compression). Added empty-history guard to `truncate_messages_safely`. New regression test [testing/test_context_fidelity.py](testing/test_context_fidelity.py) stores messages in the real production shape.
- **T-143 updated:** original self-reported fix ("bump keep_recent") was wrong; corrected with verified root cause.
- **Filed open:** T-149 (normie real history) · T-150 (compression fidelity) · T-151 (prefetch recall) · T-152 (coherence harness) · T-153 (doc drift) · T-154 (self-report confidence gate) · T-155 (VCS hygiene) · T-156 (phase freeze).

**Next step (sequenced under T-156 freeze)**
1. **T-152** — build the fake-client multi-turn coherence harness (protects every coherence fix from regressing).
2. **T-149** — give normie a real truncated message array instead of the single-message + 300-char window.
3. Then T-151 (prefetch → semantic) and T-150 (compression budget). Re-measure coherence before resuming roadmap.

## At-a-glance state

- **Verify:** PASS (R8 Stage A)
- **Open tickets:** 9 (R6 + R8 + R9 + R10 + T-092..T-094 + T-083-residual + T-095)
- **Closed total:** 78 tickets
- **74 tools** across Memory·Execution·Awareness·Project·Web·Obsidian·Image·Gmail·Calendar·Documents·Faces·Output·STT·KnowledgeGraph·BrowserAuto·Watchers·ComputerUse
- **New this session (batch 1):** BM25 hybrid retrieval · tree-sitter repo-map · cost tracker · reflect() · KG L4
- **New this session (batch 2):** Browser automation (Playwright) · Background watchers (file/URL/price/schedule) · Anthropic Computer Use desktop control
- **LLMRouter:** cost tracking + response cache added (`data/llm_cost.db`)
- **Voice:** `voice` / `voice vad` / `voice wake` commands in pi_agent · barge-in detection

## What's live

- **Universal logging** — `logs/turns.jsonl` captures every turn (both modes, every return path) via the new `process_input` wrapper around `_process_input_inner`. No more lost normie conversations.
- **Compact UX** — 3-line startup banner via `agent/startup_banner.py`. Awareness lazy-loads. Health check silent unless failures. `--verbose-init` and `--eager-awareness` flags restore legacy behaviour.
- **PI.md orchestrator** — 13 sections, ~310 lines hand-written, §4/§7/§8/§9 auto-regenerated by `scripts/refresh_pi.py` (idempotent, marker-based).
- **Sprint runner** — `scripts/sprint.py` with risk gates (RISK_FLAGGED escalates), safe-component allowlist, cost cap, ticket cap, 15-min per-ticket timeout, 1 retry on verify-fail, branch-only commits.
- **Weekly cadence** — `scripts/plan_sprint.py` (Monday) + `scripts/retro.py` (Friday). Both write vault snapshots; retro can `--notify` Telegram.
- **VS Code Foam** — `.vscode/extensions.json` + `.vscode/settings.json` recommend Foam for graph view of `PI.md`, `vault/`, `CHECKPOINTS/`, `docs/`.

## What got archived to `docs/_archive/`

CONTRADICTIONS, DEAD_CODE, FILE_INVENTORY, FINDINGS, RECONCILIATION, SCHEMA_MISMATCHES, PI_MASTER_PROMPT, root-STATUS, NEXT_SESSION, KNOWN_DEBT — 10 stale Phase-0 audit/bootstrap docs. CLAUDE.md became a 5-line pointer to PI.md.

## Perf overhaul shipped (2026-05-14)

| Fix | What changed | Impact |
|---|---|---|
| T-060 L3 cleanup | Deleted 88 test-artifact rows from l3_cache; write-path PYTEST_CURRENT_TEST guard | Prompt shrinks immediately |
| T-061 Prompt caching | `AnthropicProvider` sends `(static, dynamic)` tuple; static gets `cache_control: ephemeral`; last tool schema cached | ~5x TTFT on cache hit |
| T-062 Tool def cache | `_tool_defs_cache` built once in `__init__`, returned by ref each turn | 50-100ms/turn saved |
| T-064 Lazy imports | `agent/tools.py` uses `_LazyTool` proxy; tools imported only on first call | 16.59s → 0.09s import |
| T-063 Pi daemon | `pi_daemon.py` + `pi.py` thin client; Pi stays warm across sessions | Cold start 6-10s → <200ms |
| T-066 Sync coordinator | `_sync_lock` in `MemoryTools`; double-check TTL under lock in `get_l3_context` + `memory_read` | Eliminates double Supabase sync |
| T-067 Bg awareness | `awareness_snapshot` property triggers background `threading.Thread` refresh at 25-min mark | No more 3-8s TTL cliff |
| T-068 Async logging | `_log_queue` + `_log_worker` thread; `append_turn`, `evolution`, `memory.log_turn` all enqueued | 400-1500ms/turn saved |
| T-069 L3 budget | `max_tokens` 800→300 in `build_system_prompt_split`; SESSION TIME moved to dynamic part | ~500 fewer tokens/turn |

## OSS features added (2026-05-13)

| Feature                        | Source recipe          | File(s)                                  | Status      |
| ------------------------------ | ---------------------- | ---------------------------------------- | ----------- |
| BM25 + entity hybrid retrieval | mem0 (Apache 2.0)      | `tools/tools_memory.py`                  | live        |
| Tree-sitter repo-map           | Aider (Apache 2.0)     | `tools/tools_project.py`                 | live        |
| Cost tracking + LLM cache      | LiteLLM (MIT)          | `core/cost_tracker.py` + `llm_router.py` | live        |
| reflect() metacognitive tool   | Pi-original            | `tools/tools_project.py`                 | live        |
| Knowledge Graph L4             | Pi-original + NetworkX | `core/knowledge_graph.py`                | live        |

## New tools added (2026-05-13, batch 2)

| Feature                         | File(s)                          | Status |
| ------------------------------- | -------------------------------- | ------ |
| Browser automation (Playwright) | `tools/tools_browser_auto.py`    | live   |
| Background watchers daemon      | `agent/watchers.py`              | live   |
| Anthropic Computer Use          | `tools/tools_computer_use.py`    | live   |

## R1 (T-082) shipped 2026-05-16

| What | Where |
|---|---|
| ModeConfig dataclass + registry | [agent/modes.py](agent/modes.py) |
| Private MemoryTools (namespace + _NoopSupabase) | [tools/tools_memory.py](tools/tools_memory.py) |
| LLMRouter tier=private + Ollama provider | [core/llm_router.py](core/llm_router.py) · [core/providers/ollama.py](core/providers/ollama.py) |
| Unified `_respond_via_config` | [pi_agent.py](pi_agent.py) |
| `execute_tool(memory_override=...)` | [agent/tools.py](agent/tools.py) |
| Legacy schema → l3_cache migration | [scripts/migrate_god_memory.py](scripts/migrate_god_memory.py) |
| 5 acceptance tests | [testing/test_god_uses_unified_path.py](testing/test_god_uses_unified_path.py) |
| god.py archived (gitignored) | docs/_archive/_private/agent_god_v1.py |
| ADR | [docs/adr/001-god-as-mode-config.md](docs/adr/001-god-as-mode-config.md) |

Net active-code: −154 lines (god.py −633, additions +479). Tests +212. Privacy invariants tightened: `tickets/god/`, `vault/.god/`, `docs/_archive/_private/` excluded via `.git/info/exclude` (local-only, matches commit 41e37f2 pattern — god paths never named in public `.gitignore`).

## R5 + R7 shipped 2026-05-17

**R7 (T-088, S-065)** — SelfModifier class removed from `evolution.py` and archived to `docs/_archive/evolution_self_modifier_v1.py`. Zero callers, Phase 5 cruft. Header in the archive documents why: a future LLM-wired `modify_consciousness()` could produce an unbootable daemon by the time the user notices. Autonomous improvement now lives in `scripts/sprint.py` with proper LLM-edit + diff-review + verify gates.

**R5 (T-086, S-066)** — sprint × god isolation in 3 defence layers:

| Layer | Where |
|---|---|
| `GOD_FORBIDDEN_PATHS` constant + `_ticket_touches_god_paths()` recursive scanner | [scripts/sprint.py](scripts/sprint.py) |
| `list_open_tickets()` filters any match with `[sprint] excluding ...` log | [scripts/sprint.py](scripts/sprint.py) |
| `main()` returns rc=3 fail-fast when `tickets/open/god/` exists | [scripts/sprint.py](scripts/sprint.py) |
| AST scan asserts every `self.mode = "god"` sits inside the interactive handler | [testing/test_sprint_isolation.py](testing/test_sprint_isolation.py) |
| PI.md §10 amended with explicit forbidden-list table + policy statement | [PI.md](PI.md) |

6/6 isolation tests pass. The invariant — "god mode requires interactive entry, sprint never picks up god work" — is now code-enforced, not convention-only.

## R4 (T-085) shipped 2026-05-17

| What | Where |
|---|---|
| ADR-005: resumable exit + op-moves | [docs/adr/005-resumable-exit.md](docs/adr/005-resumable-exit.md) |
| `_ExitState` + atomic JSON state machine | [agent/session.py](agent/session.py) |
| `resume_exit_if_needed()` wired in daemon startup | [pi_daemon.py](pi_daemon.py) |
| L2→L3 promote + vault_sync moved mid-session | [pi_agent.py](pi_agent.py) `_maybe_mid_session_distill` |
| Daily memory prune cron (03:00) | [tools/tools_scheduler.py](tools/tools_scheduler.py) `_memory_prune_job` + [scripts/passive/memory_prune.py](scripts/passive/memory_prune.py) |
| Weekly audit cron (Sun 02:00) | [tools/tools_scheduler.py](tools/tools_scheduler.py) `_weekly_audit_job` + [scripts/passive/weekly_memory_audit.py](scripts/passive/weekly_memory_audit.py) |
| 10 acceptance tests + legacy-state tolerance | [testing/test_resumable_exit.py](testing/test_resumable_exit.py) |

Exit reduced from 8 → 3 ops (`flush_logs` + `session_summary` + `finalize`). Estimated exit duration: was 5-15s, now ≤2s. Crash-safe: SIGKILL mid-exit resumes on next daemon startup before `server.listen()`.

## R3 (T-084) shipped 2026-05-17

| What | Where |
|---|---|
| ADR-003: router tier matrix + TPD budget | [docs/adr/003-router-tier-and-tpd-budget.md](docs/adr/003-router-tier-and-tpd-budget.md) |
| Tier matrix (private/premium/balanced/cheap/fast + default alias) | [core/llm_router.py](core/llm_router.py) `_TIER_ORDERS` |
| `self.cerebras` direct client removed; `_respond_normie` collapsed to one router call | [pi_agent.py](pi_agent.py) |
| `distill_session(router=...)` migrated; legacy kwargs back-compat | [memory/pipeline.py](memory/pipeline.py), [agent/session.py](agent/session.py) |
| `CostTracker.tokens_today()` + `tier` column (idempotent schema migration) | [core/cost_tracker.py](core/cost_tracker.py) |
| TPD-budget preemptive brownout @ 90% utilization | [core/llm_router.py](core/llm_router.py) `_is_browned_out` |
| 9 new tests + 4 updated provider-error tests | [testing/test_router_tier_and_tpd.py](testing/test_router_tier_and_tpd.py), [testing/test_provider_error_handling.py](testing/test_provider_error_handling.py) |

Per-provider daily budgets (env-overridable): groq=100k, cerebras=1M, gemini=1M, openrouter=50k, anthropic=ollama=None. S-053's `better_future_fix` is now satisfied — one failover code path, cost-aware routing, TPD-aware brownout.

## R2.1 (T-083 partial) shipped 2026-05-17

| What | Where |
|---|---|
| ADR-002: tool registry contract | [docs/adr/002-tool-registry-pattern.md](docs/adr/002-tool-registry-pattern.md) |
| `ToolSpec` frozen dataclass | [agent/tool_spec.py](agent/tool_spec.py) |
| Registry loader + dispatch | [agent/tools.py](agent/tools.py) (1681 → 235 lines, −86%) |
| 74 tools migrated across 18 modules | tools_memory · tools_execution · tools_awareness · tools_project · tools_obsidian · tools_gmail · tools_calendar · tools_image · tools_briefing · tools_web · tools_browse · tools_media · tools_telegram · tools_tts · tools_stt · tools_browser_auto · tools_computer_use · agent/watchers.py |
| Each module's TOOLS export demonstrates the contract | `_handle_*` functions + ToolSpec list at end of module |

Adding a new tool = 1 ToolSpec append in the owning module. Already paid back this session: T-097 added `memory_search_semantic` as one ToolSpec append in tools_memory.py — no edit to agent/tools.py needed.

## Other tickets shipped this session

- **T-096** vault patterns — templates + north_star + entity hubs (`sync_entity_hubs_to_vault()` in tools/tools_obsidian.py)
- **T-097** semantic memory search — `memory_search_semantic` tool wrapping the T-080 embedding engine

## Open ticket queue

- **T-083** stays open — R2.2 (4 mergers, 73→~42 tool count) and R2.3 (audit cron) remain. Progress note in ticket.
- **R3 (T-084), R4 (T-085) …** R8 (T-089) — Hardening Track queue.
- **T-092, T-093, T-094, T-095, T-096, T-097** — feature/verification work.

## Next step

- **R2.2 mergers** when ready: each merger gets its own design check before code (decision on which old names alias, what the unified signature looks like).
- **R2.3 audit cron** independent; ~half-day job.
- **R3 (T-084)** after R2 ships fully — router-tier work no longer has to step around the elif ladder.
