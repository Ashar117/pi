# docs/UPGRADE_PLAN.md — Pi UX/Workflow/Memory Upgrade Plan

**Author:** Claude (Opus 4.7), brainstorming-only pass
**Date:** 2026-05-25
**Audience:** Future Sonnet session that will implement these one-by-one
**Status:** Not started. No code changes yet. This is the build-next reference.

---

## 0. How to use this doc

This file is the **single source of truth** for the next ~6 weeks of Pi UX work. Each upgrade is self-contained: gap → what to build → files touched → invariants that MUST NOT break → mitigations → test plan → ordered steps.

**Read order before coding anything:**
1. §1 (Context) — what Pi is, why we're doing this
2. §2 (Invariants) — the 9 things that must never break, with file refs
3. §3 (Architecture map) — what calls what
4. §4 (Upgrade catalog) — pick one, read its full entry
5. §5 (Implementation order) — recommended sequence + dependencies
6. §6 (Verification protocol) — what `verify.py` must say after each merge

**Hard rules (carry from [PI.md §1](../PI.md) and [CLAUDE.md](../CLAUDE.md)):**
- Never `git push` / `git commit` / edit `.env` without explicit "go"
- Never delete files — archive to `docs/_archive/`
- Every change goes through: ticket → test → fix → `python scripts/verify.py` (PASS) → solution log → close
- Use existing libraries; don't hand-roll
- God-path forbidden list ([PI.md §10](../PI.md)) — sprint runner must keep refusing god tickets

---

## 1. Context — what Pi is and what's missing

### What Pi is today (Phase 8.5 hardening complete)

Pi is a continuous-engineering-loop agent system. Not a chatbot. Four modes (`root`, `normie`, `god`, `research`) unified through a `ModeConfig` dataclass and a single `_respond_via_config()` path. Three-tier memory (L1 raw_wiki → L2 organized_memory → L3 l3_cache). 64 tools registered through `ToolSpec`. 91 closed tickets, 79 tests, ADRs 001–007. Last verify PASS.

### What's half-baked (Ash's complaint)

Storage is more sophisticated than ChatGPT's. **Interaction is more primitive.** Specifically:

| Gap | ChatGPT/Claude/Gemini/Claude Code have | Pi has |
|---|---|---|
| Save confirmation | "Memory updated" toast | Silent |
| Conversational save/forget | "remember…" / "forget…" intent | Must call `memory_write` tool |
| Cross-session continuation | Auto picks up where you left off | Cold start each session |
| In-conversation todo list | Visible task tracker | Tickets only (persistent, not session) |
| Artifacts / canvas | Side-panel for long code/docs | Scrolls past in terminal |
| Status line | Always visible mode/cost/state | Startup banner only |
| Inline cost per turn | Visible $ + tokens | Logged to `evolution.jsonl`, invisible |
| Plan mode | Explicit plan → approve → execute | Only in `sprint.py` (cron path) |
| Memory provenance | "based on memory I saved" | Silent injection |
| Project/ticket scoping | Per-project bubble (ChatGPT/Claude Projects) | Modes only, no scope |
| Document ingest | NotebookLM-style: drop folder → ask | One-at-a-time `read_document` |
| MCP support | 200+ community servers | Custom `ToolSpec` registry |
| Hooks (event-driven) | Claude Code hooks | Manual slash commands |
| Subagent spawn | Claude Code subagents | Modes mutually exclusive |
| Permission modes | Per-tool ask/auto/deny | Binary tool access per mode |
| File-state tracking | "did I read this before editing" | Edit without read allowed |

### The thesis

**Don't build more storage. Surface what's already there.** Pi remembers in silence. Make the memory loop visible, steerable, and conversational. Add the workflow features (plan mode, todos, status line, hooks, MCP) that make commercial agents feel polished.

---

## 2. Invariants — the 9 things that must NOT break

Anything that touches these requires extra scrutiny. Listed with file refs so Sonnet can read context before editing.

### Invariant 1 — Single response path
[pi_agent.py:897](../pi_agent.py#L897) `_respond_via_config(cfg)` is the **single** response path for all three modes (root, normie, god). T-089 R8 collapsed three parallel methods into one config-driven body. **Do not re-introduce per-mode branches inside this method.** All mode differences belong in [agent/modes.py](../agent/modes.py) `ModeConfig` fields. New modes = new entry in `MODE_CONFIGS`.

### Invariant 2 — Mode config privacy contract
[agent/modes.py:117](../agent/modes.py#L117) god mode has `public_logging=False`, `memory_db="data/god_memory.db"`, `memory_namespace="god"`. These three together keep god content out of Supabase + evolution.jsonl + raw_wiki. **Any new mode you add must explicitly set `public_logging` and `memory_namespace`.** Defaults are public.

### Invariant 3 — Tool registry uniqueness
[agent/tools.py:49](../agent/tools.py#L49) `_registry()` lazy-merges every `tools/tools_*.py` module's `TOOLS` export. **Duplicate tool names or aliases trigger an assert at import time**, crashing startup. When adding a tool, grep for the name across all `TOOLS` lists first. When renaming, use the `aliases` field for one cycle.

### Invariant 4 — Memory namespace privacy
[tools/tools_memory.py:76](../tools/tools_memory.py#L76) `MemoryTools(namespace="god")` routes Supabase calls through `_NoopSupabase` shim — every insert silently dropped, every select returns empty. **Privacy-by-file-separation, not by code path.** If you add a memory write site, never bypass `MemoryTools`; never construct a Supabase client directly outside that class.

### Invariant 5 — Sprint runner god-path refusal
[scripts/sprint.py:84](../scripts/sprint.py#L84) `GOD_FORBIDDEN_PATHS` + [scripts/sprint.py:94](../scripts/sprint.py#L94) `_ticket_touches_god_paths()` exclude god-related tickets from the sprint runner. [scripts/sprint.py:761](../scripts/sprint.py#L761) fail-fast refusal if `tickets/open/god/` exists. **Tests in [testing/test_sprint_isolation.py](../testing/test_sprint_isolation.py) guard this. Don't disable.** Autonomy × privacy is a footgun.

### Invariant 6 — Distillation fallback chain
[memory/pipeline.py:137](../memory/pipeline.py#L137) `distill_session()` falls back: `router (cheap tier) → groq → claude haiku → regex heuristic`. The heuristic is the last-resort guarantee that explicit "remember X" statements never get lost when LLMs are down. **Don't remove the heuristic.** If you add a new fallback layer, place it between router and heuristic, never below it.

### Invariant 7 — Exit step idempotency
[agent/session.py:70](../agent/session.py#L70) `EXIT_STEPS` is the canonical ordered list. [agent/session.py](../agent/session.py) `_run_exit_steps()` tolerates legacy step names (marks completed) so a state file from an older session resumes cleanly. **Renaming a step breaks resume of in-flight sessions.** Adding a step = append to `EXIT_STEPS` + add body to `_EXIT_STEP_BODIES`. Each body must be idempotent.

### Invariant 8 — L3 schema migrations
[tools/tools_memory.py:156](../tools/tools_memory.py#L156) `_init_sqlite()` uses `PRAGMA table_info` + conditional `ALTER TABLE` for idempotent column additions (T-078 `invalid_at`, T-125a `kind/source_id/recompute_after/formula`). **All new L3 columns must follow this pattern.** Never recreate the table; never drop columns.

### Invariant 9 — Turn log non-fatal contract
[agent/turn_log.py:173](../agent/turn_log.py#L173) `append_turn()` **must never raise.** Every turn (both modes, all return paths) appends to `logs/turns.jsonl` + increments the per-day SQLite counter. A disk-full or permission error logs and continues. [pi_agent.py:404](../pi_agent.py#L404) `process_input()` wraps `_process_input_inner` so an inner exception still produces a turn log entry with `error=...`.

### Bonus invariants (read before touching these areas)

- **L1 thread UUID determinism:** [pi_agent.py:176](../pi_agent.py#L176) `self.l1_thread_id = uuid5(NAMESPACE_DNS, session_id)` — auto-log and tool-path L1 writes share the same `thread_id` for reconstruction. Don't generate new UUIDs in either path.
- **Mode switch loose-match:** [agent/modes.py:18](../agent/modes.py#L18) `detect_mode_switch()` biases toward switching. False negatives strand users in the wrong mode → L1/L2 hallucinations (T-019). Don't tighten the regex.
- **Prompt cache split:** [agent/prompt.py:100](../agent/prompt.py#L100) `build_system_prompt_split()` returns `(static, warm, dynamic)` for Anthropic's 3-segment cache (T-091). Adding to `warm` invalidates the L3 cache point. New per-turn context belongs in `dynamic`.
- **Async log worker:** [pi_agent.py:328](../pi_agent.py#L328) `_log_worker()` drains a queue. Failed writes fall through to [pi_agent.py:342](../pi_agent.py#L342) `_save_dropped_log()` → `logs/dropped_turns.jsonl`. [memory/pipeline.py:69](../memory/pipeline.py#L69) `_drain_dropped_turns()` replays them at next distill. **Don't break this recovery loop.**
- **Tool input validation:** [agent/tools.py:207](../agent/tools.py#L207) `_validate_tool_input()` uses jsonschema. A broken input_schema in your new tool will reject every call with `invalid_input` — test your schema before shipping.

---

## 3. Architecture map (the 30-second tour)

```
pi.py / pi_daemon.py
        │
        ▼
PiAgent  ────────────► process_input(user_input)
(pi_agent.py)               │
                            ▼
                     _process_input_inner
                            │
            ┌───────────────┼────────────────┐
            ▼               ▼                ▼
   detect_mode_switch   special cmds     _respond_via_config(cfg)
   (agent/modes.py)     (help, exit,           │
                         briefing,             │
                         research)             │
                                               ▼
                             ┌─────────────────┴─────────────────┐
                             │  agent/awareness_shortcut.py      │
                             │  agent/prompt.py:build_split      │
                             │  agent/tools.py:execute_tool      │
                             │  core/llm_router.py:chat          │
                             │  agent/turn_log.py:append_turn    │
                             │  memory/pipeline.py:distill (bg)  │
                             │  tools/tools_obsidian:sync_vault  │
                             └───────────────────────────────────┘
```

### Memory pipeline

```
L1 raw_wiki (Supabase)
    │     ┌────────────────────────────────────────────┐
    │     │ written by:                                │
    │     │   - mem.log_turn() every turn               │
    │     │   - dropped_turns.jsonl on Supabase failure │
    │     └────────────────────────────────────────────┘
    │
    ▼ distill_session() (router → groq → haiku → heuristic)
    │   fires: every 10 turns (mid-session) + on exit
    │
L2 organized_memory (Supabase)
    │   - semantic dedup (Gemini embed + Haiku tiebreak)
    │   - lexical dedup (prefix match)
    │   - per-row metadata: source, session_id, embedding
    │
    ▼ promote_l2_to_l3(importance >= 8)
    │
L3 l3_cache (SQLite, data/pi.db)
    │   - hot context injected into system prompt warm segment
    │   - 800-token budget via get_l3_context()
    │   - BM25 + entity hybrid search via memory_read
    │   - caretaker.lite() recomputes derived facts (age, days_until)
    │   - PRAGMA-idempotent migrations
    │
    ▼ vault sync (one-way, write-files idempotent)
    │
vault/memory/L3/*.md  +  vault/memory/L2/*.md  (Obsidian-readable mirror)
```

### Tool registration (T-083)

```
tools/tools_<category>.py
    TOOLS = [
        ToolSpec(name=..., description=..., input_schema=..., handler=..., aliases=()),
        ...
    ]
        │
        ▼  lazy-merged by agent/tools.py::_registry()
        ▼
agent/tools.py::execute_tool(name, input)
        │
        ├── validates input vs spec.input_schema (jsonschema)
        ├── dispatches to spec.handler(agent, input, memory_override=...)
        ├── evaluates spec.success_predicate(result)
        └── evolution.track_pattern(tool_<name>, success, metadata)
```

### Vault structure

```
vault/
├── _hot.md              ← session-start context, 60 lines (HOT tier)
├── memory/
│   ├── L2/              ← one file per category (cold mirror of organized_memory)
│   └── L3/              ← one file per category (warm mirror of l3_cache)
├── notes/
│   ├── status.md
│   ├── north_star.md
│   ├── tickets/         ← open.md + closed.md
│   ├── per-ticket/      ← T-NNN-slug.md per closed ticket (WARM, gitignored)
│   ├── sessions/        ← per-session summaries
│   └── templates/       ← Decision/Entity/Session/Ticket/North-Star templates
└── README.md
```

**Sync direction:** one-way at exit + every 10 turns (`tools/tools_obsidian.py::sync_vault()`). Vault is a **read cache**; Supabase + SQLite are source of truth.

---

## 4. Upgrade catalog

Sixteen upgrades, three tiers. Each carries:

- **Gap:** what competitor has, what Pi lacks
- **Build:** the concrete artifact
- **Files:** what gets touched (with line refs where known)
- **Invariants at risk:** which §2 invariants could break
- **Mitigations:** how to avoid breakage
- **Tests:** what to add under `testing/`
- **Steps:** ordered sub-tasks for Sonnet

---

### TIER 1 — High leverage, low blast radius

These are the visible UX wins. Each is < 1 day's work. Do them all first; Pi will feel dramatically less half-baked.

---

#### Upgrade 1 — Memory save/forget conversational handle + toast

**Gap.** ChatGPT shows a "Memory updated" toast when it saves a fact. Pi extracts facts silently via session-end distillation; the user has zero feedback. ChatGPT/Claude also handle `"remember X"` / `"forget Y"` as conversational shortcuts; Pi requires the LLM to call `memory_write` (which it often doesn't, or does in the wrong tier).

**Build.**
1. An **intent detector** that runs in `_process_input_inner` BEFORE `_respond_via_config`. Detects: `remember (that)? X`, `save (that)? X`, `don't forget X` → write directly to L3 with `importance=7`, `category=note`, `source=stated_explicit`. Detects: `forget (that)? X`, `delete X from memory` → semantic-search L3 → confirm match → mark with `_invalidate_l3_entry`.
2. A **toast renderer.** When `memory_write` or `_invalidate_l3_entry` is called from anywhere (intent detector, tool call, distill), append a single line to the agent's response: `[memory: saved 'first 60 chars...' · id=abc123 · type "forget abc" to undo]`.

**Files.**
- New: [agent/memory_intent.py](../agent/memory_intent.py) — detector module
- New: [agent/memory_toast.py](../agent/memory_toast.py) — toast formatter
- Edit: [pi_agent.py:502](../pi_agent.py#L502) `_process_input_inner` — call intent detector first; if hit, run write + return response with toast instead of calling LLM
- Edit: [tools/tools_memory.py:476](../tools/tools_memory.py#L476) `memory_write` + [tools/tools_memory.py:1094](../tools/tools_memory.py#L1094) `_invalidate_l3_entry` — emit a toast event via a global event bus (or just return enough info for the caller to format)
- New: [testing/test_memory_intent.py](../testing/test_memory_intent.py)
- New: [testing/test_memory_toast.py](../testing/test_memory_toast.py)

**Invariants at risk.**
- **Inv. 1** (single response path) — the intent detector is BEFORE `_respond_via_config`, not inside. It either short-circuits (returns response immediately) or falls through. Safe.
- **Inv. 4** (memory namespace privacy) — intent detector must check `cfg.memory_namespace` to write to the right DB. If in god mode, write to `god_memory.db`.
- **Inv. 9** (turn log non-fatal) — intent-handled turn still appends to turn log. Make sure `process_input()` wrapping still fires.

**Mitigations.**
- Intent detector behind a simple regex set. Errors caught → fall through to normal `_respond_via_config`. Never raise to user.
- Toast format is a single line, prepended with `[memory: ...]`. Filterable.
- Per-mode: in god mode, toast goes to stderr only (no public log).

**Tests.**
- `test_memory_intent.py`: 12 phrasings of "remember…" all hit detector. Negative cases ("I'll remember to call mom" — not an instruction, skip). Returns `(intent, payload)` tuple cleanly.
- `test_memory_toast.py`: toast format stable, line under 120 chars, includes id prefix.
- Integration: `test_memory_intent_writes_to_l3.py` — full path, including verifying L3 row exists.

**Steps for Sonnet.**
1. File ticket `T-130-conversational-memory-handle`.
2. Write `agent/memory_intent.py` with `detect_memory_intent(text) -> Optional[Intent]`. Intents: `SAVE(content, category, importance)`, `FORGET(query)`.
3. Write tests for intent detector first. Red-then-green.
4. Write `agent/memory_toast.py::format_toast(action, id, preview) -> str`.
5. Hook into `_process_input_inner` after `detect_mode_switch`, before special-command block. If intent detected: handle, append toast, return.
6. Modify `_invalidate_l3_entry` to accept optional `reason` field for the toast.
7. Run `python scripts/verify.py`; must PASS.
8. Manual test in `python pi.py`: type "remember I prefer terse responses", check L3 has new row + toast appeared.

**Effort:** half a day.

---

#### Upgrade 2 — Session-start "where we left off" continuation banner

**Gap.** ChatGPT/Claude seamlessly continue. Pi's startup banner shows tool count, turns today, ticket count — but not "last session you were working on T-099 / discussed memory schema / wrote 3 facts to L3."

**Build.**
A 4th line to the startup banner reading from:
- Most recent `category=session_history` row in L3 (the session summary written by `_do_session_summary`)
- Most recent ticket touched (from `tickets/closed/*.json` modified time)
- Optionally: 2 most recent L2 writes from prior session

Output: `Last session 2026-05-24 14:32 · "discussed UX upgrades, decided plan-mode first" · 3 new memories`

**Files.**
- Edit: [agent/startup_banner.py:49](../agent/startup_banner.py#L49) `format_banner` — add `_format_continuation_line()` → appended as line 4 or 5
- Read: [agent/session.py:277](../agent/session.py#L277) `_do_session_summary` for the L3 row format
- New: [testing/test_startup_continuation.py](../testing/test_startup_continuation.py)

**Invariants at risk.**
- None directly. Read-only.

**Mitigations.**
- All reads in try/except → return "" on failure. Continuation is best-effort.
- Cap text at 80 chars to keep banner scannable.

**Tests.**
- Mock L3 with 0/1/multiple session_history rows; verify correct rendering.

**Steps for Sonnet.**
1. Ticket `T-131-startup-continuation-banner`.
2. Add `_recent_session_summary()` helper in `startup_banner.py` querying SQLite directly (no MemoryTools dep — keeps it import-safe).
3. Append to `lines_out` after the existing 4th audit line.
4. Test.
5. Run `verify.py`.

**Effort:** ~2 hours.

---

#### Upgrade 3 — Per-turn status line repaint (CLI only)

**Gap.** Claude Code's status line is always visible. Pi only paints a startup banner.

**Build.**
After every turn in CLI mode (`pi.py`), emit a one-line status footer:
```
[root · turn 14 · session a3f2e1c · $0.038 today · 2 open · L3: 184 rows]
```
Toggleable via env: `PI_STATUS_LINE=on`. Telegram path unaffected.

**Files.**
- Edit: [pi_agent.py:1259](../pi_agent.py#L1259) `run()` — after each `print(response)`, conditionally print status line
- New: [agent/status_line.py](../agent/status_line.py) — pure formatter `format_status_line(agent) -> str`
- New: [testing/test_status_line.py](../testing/test_status_line.py)

**Invariants at risk.**
- None. Pure output after the response.

**Mitigations.**
- ENV-gated default off. Telegram/voice paths don't call it.
- Wrap in try/except; on failure print nothing rather than crash the turn.

**Tests.**
- Pure formatter test: given mock agent state, output string matches expected.

**Steps for Sonnet.**
1. Ticket `T-132-status-line`.
2. Write `agent/status_line.py::format_status_line(agent)`.
3. In `PiAgent.run()`, after the `print(response)` call, check `os.environ.get("PI_STATUS_LINE")` and print.
4. Document in [README.md](../README.md) under Modes or Commands.

**Effort:** ~2 hours.

---

#### Upgrade 4 — Inline cost footer per turn

**Gap.** Claude Code shows `$0.04 · 8.2k tokens` per turn inline. Pi logs to `evolution.jsonl` after-the-fact; user never sees cost during the turn.

**Build.**
At the end of every root-mode turn, append `[$0.0034 · 8120 tok in · 1240 out · sonnet · 1.4s]` to the response (or print on next line). ENV-gated `PI_SHOW_COST=on`.

**Files.**
- Edit: [pi_agent.py:1062](../pi_agent.py#L1062) `_respond_via_config` step 6 — already computes `t_in`, `t_out`, `total_cost`, `duration_s`. Conditionally print a one-line footer before returning.
- New: [testing/test_cost_footer.py](../testing/test_cost_footer.py)

**Invariants at risk.**
- None. Read-only print.

**Mitigations.**
- Behind env flag (default off).
- Print to stderr in CLI mode; never include in returned `final_text` so it doesn't pollute Telegram/voice output.

**Tests.**
- Mock LLM response with token counts; verify footer string format.

**Effort:** ~1 hour.

---

#### Upgrade 5 — `pi memory` CLI (list / forget / pin)

**Gap.** ChatGPT lets you open Settings → Personalization → see + edit memories. Pi's L2/L3 are databases the user can't easily inspect or steer. **Trust comes from visibility.**

**Build.**
Standalone script `scripts/memory_cli.py` with subcommands:
- `pi memory list [--tier l2|l3] [--category X] [--limit N]` — print rows
- `pi memory forget <query>` — semantic search → confirm → invalidate
- `pi memory pin <id>` — bump importance to 10, mark unprunable
- `pi memory why <topic>` — show provenance trace (session_id, source, created_at)

Optionally also a thin shell shim `pi.py memory <subcommand>` that dispatches into the same module.

**Files.**
- New: [scripts/memory_cli.py](../scripts/memory_cli.py)
- New: [testing/test_memory_cli.py](../testing/test_memory_cli.py)
- Edit: [README.md](../README.md) — document under Commands

**Invariants at risk.**
- **Inv. 4** (privacy) — by default, CLI operates on public DB (`data/pi.db`). Add `--god` flag that requires `PI_GOD_CLI=1` env to access `god_memory.db`. Never default to god.
- **Inv. 8** (L3 schema) — only use `MemoryTools` methods; never raw SQL writes.

**Mitigations.**
- All writes (forget, pin) confirm before proceeding (`--yes` to skip).
- Display row id prefix only (8 chars) to avoid copy/paste leaks of full UUIDs.

**Tests.**
- `pi memory list` against a seeded test DB returns expected rows.
- `pi memory forget` calls `_invalidate_l3_entry` and verifies row's `invalid_at` is set.
- `--god` without env flag refuses with helpful error.

**Steps for Sonnet.**
1. Ticket `T-133-memory-cli`.
2. Use `argparse` with subparsers.
3. Reuse `MemoryTools` — instantiate with `namespace="pi"` by default, `namespace="god"` if `--god` flag + env.
4. Each subcommand is a top-level function for testability.
5. Run tests, run verify.

**Effort:** ~half a day.

---

### TIER 2 — Real wins, slightly bigger

Each is 1–3 days. These move Pi from "polished CLI" to "feels like a real product."

---

#### Upgrade 6 — In-session visible TodoWrite-equivalent

**Gap.** Claude Code's todo list (✓ / in_progress / pending) is the single best UX signal. Pi has tickets (cross-session persistence) but nothing for "this conversation's open threads."

**Build.**
- A `SessionTodos` singleton on `PiAgent` holding `[{id, content, status}]`.
- A slash command `/todo add <text>`, `/todo done <id>`, `/todo list`.
- Auto-render after every turn in CLI: `Active: 1) Refactor X · in_progress  2) Write tests · pending`.
- Persisted ephemeral: L3 write with `category=session_todo`, `expiry=session_end`. Pruned on `on_exit`.

**Files.**
- New: [agent/session_todos.py](../agent/session_todos.py)
- Edit: [pi_agent.py](../pi_agent.py) — add `self.todos = SessionTodos()`, slash command dispatch in `_process_input_inner` after special commands
- Edit: [agent/session.py](../agent/session.py) — clear `session_todo` L3 rows in `on_exit`
- New: [testing/test_session_todos.py](../testing/test_session_todos.py)

**Invariants at risk.**
- **Inv. 8** (L3 schema) — using existing `category` column, no schema change.
- Risk of polluting L3 if exit doesn't clean. Mitigation: `expiry` set so prune-tick catches them daily even if exit fails.

**Mitigations.**
- Session_todo rows have `expiry=now+24h` so they auto-expire even if `on_exit` skips.
- In god mode, todos go to `god_memory.db` (per `_get_memory_for_config`).

**Tests.**
- Add → list shows new entry.
- Done → status transitions, list shows strikethrough.
- Exit → all session_todo rows have `active_until` in past.

**Effort:** 1 day.

---

#### Upgrade 7 — Query-conditional memory injection per turn

**Gap.** ChatGPT dynamically retrieves relevant past chats per query (the "chat history reference" layer). Pi's L3 is *static-ish* (refreshed at session start, not per-turn). The `_prefetch_memory` exists but only fires on recall-question patterns — most turns get no contextual retrieval.

**Build.**
On every turn (in `_respond_via_config` before LLM call):
1. Embed `user_input` via Gemini (~$0.00001).
2. Cosine top-3 against L2 rows with embeddings.
3. If top score > 0.78, inject as `[CONTEXT: 'fact text']` block in `dynamic` prompt segment.

This **replaces** the current keyword-extracting `_prefetch_memory` for L2 (keep the L3 keyword path as a complementary cheap pass).

**Files.**
- Edit: [pi_agent.py:647](../pi_agent.py#L647) `_prefetch_memory` — add semantic path after the existing keyword path
- Reuse: [memory/semantic_dedup.py:84](../memory/semantic_dedup.py#L84) `get_embedding`
- Reuse: [memory/semantic_dedup.py:116](../memory/semantic_dedup.py#L116) `cosine_similarity`
- New: [testing/test_semantic_prefetch.py](../testing/test_semantic_prefetch.py)

**Invariants at risk.**
- **Bonus inv: prompt cache split** — `dynamic` segment is fine (it changes per turn anyway). Don't put this in `warm`.
- Latency: Gemini embed call adds ~150–300ms per turn. Mitigation: timeout 400ms; on timeout, skip.

**Mitigations.**
- Wrap in try/except + 400ms timeout via concurrent.futures.
- Cache the last embedded query (LRU 16). If user paraphrases, hit cache.
- Skip for very short inputs (<3 words).
- Skip for awareness-shortcut path (already short-circuits).

**Tests.**
- Mock Gemini embed → injected context appears in system prompt.
- Timeout simulation → no injection, no exception.
- Empty/short input → skipped.

**Effort:** 1 day.

---

#### Upgrade 8 — Memory provenance tag

**Gap.** ChatGPT will say "based on a memory you saved." Pi silently injects L3 facts; user has no idea what came from memory vs. what the model invented.

**Build.**
When `_prefetch_memory` or the new query-conditional retrieval injects a fact, format it as:
```
[CONTEXT — from memory you wrote on 2026-04-15 (importance 8):]
"Ash prefers terse responses, lowercase, no emojis."
[/CONTEXT — say "forget that" to remove]
```
The model is instructed (in consciousness.txt) to optionally cite as `[mem: ...]` when its answer depends on injected context.

**Files.**
- Edit: [pi_agent.py:647](../pi_agent.py#L647) `_prefetch_memory` — change format string
- Edit: [prompts/consciousness.txt](../prompts/consciousness.txt) — add 3-line section on `[CONTEXT]` blocks + when to cite
- Reuse: existing memory_intent forget handler for "forget that" callback
- New: [testing/test_provenance_format.py](../testing/test_provenance_format.py)

**Invariants at risk.**
- **Bonus inv: prompt cache** — adding to system prompt invalidates cache on first turn after upgrade. One-time cost.
- Consciousness.txt is in [PI.md §10](../PI.md) risk-flagged list ("propose diff first"). Sonnet must show Ash the diff before editing.

**Mitigations.**
- Provenance line is ~2 lines per fact, capped at 3 facts per turn → ~6 lines max.
- Test that existing turn outputs still pass through.

**Effort:** ~half a day.

---

#### Upgrade 9 — Plan mode

**Gap.** Claude Code has `Plan` mode — before risky changes, model produces a plan, asks for approval, then executes. Pi's `sprint.py` does this (cron path) but interactive Pi just *does*.

**Build.**
- New mode `plan` in `MODE_CONFIGS`. Same as `root` BUT `supports_tools=False`, addendum system prompt: "Produce a numbered plan. List files you'd touch, tests you'd add, risk level. Wait for 'go' or 'cancel'. Do nothing else."
- Trigger: type `plan mode` (extends `detect_mode_switch` SWITCH_VERBS / words set).
- On user response containing "go" / "approve" / "proceed" → switch to `root`, replay last user input + plan as context, execute.
- `cancel` / `no` / `change X` → stay in plan, regenerate.

**Files.**
- Edit: [agent/modes.py:117](../agent/modes.py#L117) `MODE_CONFIGS` — add `"plan"` entry. `tool_allowlist=()`, `prompt_path="prompts/consciousness_plan.txt"`, `public_logging=True`.
- Edit: [agent/modes.py:18](../agent/modes.py#L18) `detect_mode_switch` — add `"plan"` to recognised words.
- New: [prompts/consciousness_plan.txt](../prompts/consciousness_plan.txt) — slim ~80-line prompt
- Edit: [pi_agent.py:502](../pi_agent.py#L502) `_process_input_inner` — handle approve/cancel transitions in plan mode
- New: [testing/test_plan_mode.py](../testing/test_plan_mode.py)

**Invariants at risk.**
- **Inv. 1** (single response path) — plan mode flows through `_respond_via_config` like any other mode. Good.
- **Inv. 2** (privacy contract) — `public_logging=True`, `memory_namespace="pi"`. Plans are public.
- **Bonus: mode switch loose-match** — adding "plan" word risks false positive ("I have a plan for tomorrow" → switches modes). Mitigation: only triggers with `mode` word OR a switch verb OR ≤3 word message.

**Mitigations.**
- The approve/cancel handler is in `_process_input_inner`, BEFORE `_respond_via_config`. Keeps `_respond_via_config` clean.
- Last plan stored in `self._pending_plan` — cleared after execute or cancel or 30 min timeout.

**Tests.**
- "switch to plan mode" → mode switches.
- "build me a feature for X" in plan mode → response contains "Plan:" header, no file writes.
- "go" → executes, mode auto-switches back to root.
- Cancel → stays in plan, plan discarded.

**Effort:** 2 days.

---

#### Upgrade 10 — Per-project / per-ticket memory scoping

**Gap.** ChatGPT Projects scope memory. Pi has modes only; every conversation sees all L2 facts. When focused on T-099, general facts (gym preferences, gmail context) pollute retrieval.

**Build.**
- Add optional `scope` column to L2 (`organized_memory.metadata.scope`) and L3 (`l3_cache.scope`).
- New slash command `/scope T-099` or `/scope gnn-research` — sets `agent.current_scope`. Memory reads filter by `scope IN (current_scope, NULL)`. Memory writes tag with current_scope.
- `/scope clear` resets.

**Files.**
- Edit: [tools/tools_memory.py:156](../tools/tools_memory.py#L156) `_init_sqlite` — add `scope TEXT` column via the idempotent PRAGMA pattern
- Edit: [tools/tools_memory.py:200](../tools/tools_memory.py#L200) `memory_read` — add `scope` filter
- Edit: [tools/tools_memory.py:476](../tools/tools_memory.py#L476) `memory_write` — accept `scope` kwarg
- Edit: [pi_agent.py](../pi_agent.py) — add `self.current_scope = None`, slash dispatch
- Reuse: existing L2 metadata-in-content JSONB pattern (no Supabase migration needed)
- New: [testing/test_memory_scope.py](../testing/test_memory_scope.py)

**Invariants at risk.**
- **Inv. 8** (L3 schema) — add column via PRAGMA + ALTER pattern. Idempotent.
- Existing rows have NULL scope → must appear in all queries (treated as "global"). Test this explicitly.

**Mitigations.**
- Default `scope=None` for writes when `current_scope` is unset → backward compat.
- `_is_l3_duplicate` should compare within same scope (else legitimate ticket-local fact gets dropped as duplicate of global).

**Tests.**
- Write with scope=T-099; read with scope=T-099 returns it; read with no scope also returns it.
- Read with scope=T-100 does NOT return T-099 rows but DOES return global rows.
- Existing rows (scope NULL) remain queryable.

**Effort:** 2 days.

---

### TIER 3 — Bigger lifts (each 3+ days)

Worth doing but only after Tier 1 + 2 ship.

---

#### Upgrade 11 — NotebookLM-style document ingest with citations

**Gap.** Drop a folder of PDFs / URLs → ask questions across all of them with inline citations. Pi has `read_document` (one at a time), nothing for "ingest this corpus."

**Build.**
- New tool module `tools/tools_ingest.py` exporting `TOOLS = [pi_ingest, pi_query_kb, pi_list_kb]`.
- New SQLite table `session_kb` (chunked text + Gemini embeddings + source path + chunk_id).
- `pi ingest <path>` (CLI shim too) walks folder, extracts text (existing `read_document` for PDFs), chunks, embeds, stores.
- `pi_query_kb(query)` retrieves top-K chunks, returns text + `[source: file.pdf:chunk_4]` citations.
- Auto-integration with `memory_read`: if `current_scope` matches a KB tag, KB takes precedence.

**Files.**
- New: [tools/tools_ingest.py](../tools/tools_ingest.py)
- New: [memory/session_kb.py](../memory/session_kb.py) — KB ops
- Reuse: [memory/semantic_dedup.py](../memory/semantic_dedup.py) embedding + cosine
- Reuse: [tools/tools_media.py](../tools/tools_media.py) `read_document`
- New: [testing/test_ingest.py](../testing/test_ingest.py)

**Invariants at risk.**
- **Inv. 3** (tool registry uniqueness) — `pi_ingest` is new; grep for collisions.
- **Inv. 4** (memory namespace privacy) — KB lives in `data/session_kb.db` (or `data/god_session_kb.db` in god mode). Add `namespace` param like MemoryTools.

**Mitigations.**
- KB DB lives in `data/`, separate from `pi.db` — keeps L1/L2/L3 schema clean.
- Retention: optional `--ttl 30d` flag, expire chunks past TTL.

**Tests.**
- Ingest a 5-page PDF, query for a specific fact → citation points to right chunk.
- Mixed query (Pi memory + KB) → both sources surface with type tags.

**Effort:** 3 days.

---

#### Upgrade 12 — Hook system (event-driven automation)

**Gap.** Claude Code has hooks that fire on events (PostCommit, PrePush, PostTurn). Pi has 6 passive observer scripts (`/privacy`, `/session-check`, etc.) but they're manual. Hooks let Pi self-police.

**Build.**
- New `agent/hooks.py` with HookRegistry + event dispatch.
- New `data/hooks.json` config: `{"events": {"post_commit": [{"name": "privacy", "cmd": "python scripts/passive/privacy_publish_guard.py", "timeout": 10}], ...}}`.
- Events to wire: `post_turn`, `pre_exit`, `post_exit`, `post_commit`, `post_distill`, `post_verify`.
- Hooks run in subprocess with timeout. Failures logged via `track_silent`, never block.

**Files.**
- New: [agent/hooks.py](../agent/hooks.py)
- New: [data/hooks.json](../data/hooks.json) — empty template
- Edit: [pi_agent.py:404](../pi_agent.py#L404) `process_input` — fire `post_turn` event
- Edit: [agent/session.py:251](../agent/session.py#L251) `on_exit` — fire `pre_exit` + `post_exit`
- Edit: [scripts/sprint.py:528](../scripts/sprint.py#L528) `commit_branch` — fire `post_commit`
- New: [testing/test_hooks.py](../testing/test_hooks.py)

**Invariants at risk.**
- **Inv. 9** (turn log non-fatal) — hooks fire AFTER turn log. A hook crash never affects the turn record.
- A misconfigured hook crashing the agent. Mitigation: all hooks subprocess + timeout + observability.

**Mitigations.**
- Hook config validated on agent start; invalid entries skipped with log.
- Per-event timeout default 10s; configurable.
- Hooks always run async (daemon thread) for `post_*` events; only `pre_*` events block.

**Tests.**
- Hook with valid command → fires + records exit code.
- Hook that times out → logged via `track_silent`, doesn't block.
- Missing hooks.json → no-op, no error.

**Effort:** 3 days.

---

#### Upgrade 13 — MCP client adapter (highest leverage tool-side)

**Gap.** Pi has 64 hand-built tools. Every integration is custom. The MCP ecosystem has 200+ servers (GitHub, Notion, Linear, Slack, Postgres, Stripe, Sentry, …). Speaking MCP absorbs all of them for free.

**Build.**
- New `tools/mcp_adapter.py` that:
  1. Reads `data/mcp_servers.json` — list of MCP server configs (command, args, env).
  2. Spawns each as subprocess on agent startup.
  3. Calls each server's `tools/list` and registers them as `ToolSpec` instances in the existing registry.
  4. `handler` for each routes through the MCP transport (JSON-RPC over stdio).
- Optional: `/mcp list` / `/mcp reload` slash commands.

**Files.**
- New: [tools/mcp_adapter.py](../tools/mcp_adapter.py)
- New: [data/mcp_servers.json](../data/mcp_servers.json) — empty template
- Edit: [agent/tools.py:23](../agent/tools.py#L23) `_TOOL_MODULES` — add `tools.mcp_adapter` so registry picks up MCP-registered specs
- Use library: `mcp` (https://pypi.org/project/mcp/) — Anthropic-maintained Python client. Don't hand-roll the protocol.
- New: [testing/test_mcp_adapter.py](../testing/test_mcp_adapter.py)

**Invariants at risk.**
- **Inv. 3** (tool registry uniqueness) — MCP-registered tools are namespaced (`mcp_<server>_<tool>`) to avoid collisions.
- An MCP server hangs → agent hangs. Mitigation: per-call timeout + circuit breaker (reuse `agent/provider_router.py` ProviderRouter pattern).
- **Inv. 4** (privacy) — in god mode, `_filtered_tool_defs` filters by `tool_allowlist`. MCP tools NOT in allowlist won't show up in god. Don't bypass.

**Mitigations.**
- Subprocess per server; killed on agent exit (atexit hook).
- 10s timeout per MCP call.
- `mcp_servers.json` validated on load.

**Tests.**
- Mock MCP server with 2 tools → both appear in registry.
- Tool call → JSON-RPC roundtrip works.
- Server crash → tools removed from registry, agent continues.

**Effort:** 3–5 days. (The Python `mcp` package is well-documented; integration is the cost.)

---

#### Upgrade 14 — Subagent spawn

**Gap.** Claude Code spawns specialized agents (Explore, Plan) for parallel research. Pi's 4 modes are mutually exclusive — can't delegate.

**Build.**
- New method `PiAgent.spawn_subagent(prompt, tier="cheap", depth=1) -> str`.
- Forks a thread-local PiAgent state: same `MemoryTools` (read-only), fresh `messages`, fresh `session_id` (parent's session prefixed with `child-`).
- Runs N turns (default 1, max 5), returns the final text.
- Depth-limited: refuses if `depth > 2` to prevent infinite recursion.
- Cost-bounded: hard cap $0.10 per spawn.

**Files.**
- New: [agent/subagent.py](../agent/subagent.py)
- Edit: [pi_agent.py](../pi_agent.py) — add `spawn_subagent` method + tool wrapper if exposing to LLM
- New: [testing/test_subagent.py](../testing/test_subagent.py)

**Invariants at risk.**
- **Inv. 4** (privacy) — subagent inherits parent's `memory_namespace`. Never escalate (god parent → public subagent OK; public parent → god subagent FORBIDDEN).
- **Inv. 6** (distillation fallback) — subagent shares parent's router → same fallback chain.
- Risk: runaway costs. Mitigation: hard cost cap + max-turn cap.

**Mitigations.**
- Subagent doesn't write to L1/L2 (read-only memory access).
- All subagent turns logged with `parent_session_id` for traceability.

**Tests.**
- Spawn a subagent that searches code → returns sensible text.
- Recursion limit triggers refusal.
- Cost cap triggers cancel + partial result.

**Effort:** 3 days.

---

#### Upgrade 15 — Artifact / canvas pattern

**Gap.** Claude.ai's artifact side-panel keeps long code/docs out of chat. Pi dumps everything to terminal where it scrolls past.

**Build.**
- When LLM output contains a code block > 30 lines OR a markdown doc > 50 lines, **don't** include the full content in the response.
- Instead: write to `scratch/<session_id>/<artifact-N>.{py|md|...}`, return only `[artifact: scratch/.../auth.py · 142 lines · diff +12/-3]`.
- New slash command `/show <n>` prints the artifact inline.
- New slash command `/diff <n>` shows last change.

**Files.**
- New: [agent/artifacts.py](../agent/artifacts.py)
- Edit: [pi_agent.py:1062](../pi_agent.py#L1062) `_respond_via_config` step 6 — call `extract_artifacts(final_text) -> (clean_text, artifacts_written)`, append summary
- New: [testing/test_artifacts.py](../testing/test_artifacts.py)

**Invariants at risk.**
- Risk: breaks scripts/tests that grep stdout for code. Mitigation: ENV-gated default OFF.
- Telegram path: artifacts.md in Telegram is fine (single message); large code might be better as attachment. Don't enable for Telegram in v1.

**Mitigations.**
- Behind `PI_ARTIFACTS=on`.
- Artifacts auto-cleaned on session exit (older than 7 days).

**Tests.**
- Long code block → file written, response truncated.
- Short code block (< threshold) → unchanged.

**Effort:** 2 days.

---

### TIER 4 — Brain-inspired memory upgrades (separate axis)

These aren't UX wins — they're **memory-quality upgrades** inspired by what neuroscience actually says about human memory (not pop-science). Pi's L1/L2/L3 already maps to working / episodic / semantic memory, and `distill_session` is already a consolidation pass. The four gaps below are real omissions, not metaphor-chasing.

**Critical framing:** These are *additions* to existing schema/flow, not a rebuild. Do NOT tear out L1/L2/L3. Augment metadata, layer in new triggers.

**Order:** Tier 4 is orthogonal to Tier 1–3. Don't block on it; pick up these tickets when Tier 1 ships and the UX feels real. T-134 is the prerequisite — others depend on it.

---

#### Upgrade 17 — Multi-dimensional salience scoring (T-134)

**Gap.** Every L2/L3 row has one int `importance` 1–10. Humans tag memories on **recency × emotional valence × novelty × surprise × goal-relevance**. A fact about your dying grandma and a fact about your favorite color don't get the same weight just because both are "8."

**Build.**
Add four scalar fields to L2 + L3 metadata:
- `surprise_score` — how unexpected was this fact given existing L2? Computed at write time as `1 - max(cosine_similarity)` against same-category rows. High surprise = high novelty.
- `goal_alignment_score` — does it match `current_scope` or any active ticket? Cheap string-match boost.
- `recency_weight` — exponential decay from `created_at` (refreshed on `last_accessed_at` bump).
- `affect_tag` — optional discrete label: `neutral` / `important` / `painful` / `joyful` / `urgent`. Either user-tagged (toast lets you add) or LLM-classified at distill.

Define **composite salience**: `salience = 0.3·importance + 0.25·surprise + 0.2·goal_alignment + 0.15·recency + 0.1·affect_bonus`.

Replace `importance DESC` ordering in retrieval paths with `salience DESC`.

**Files.**
- Edit: [tools/tools_memory.py:156](../tools/tools_memory.py#L156) `_init_sqlite` — PRAGMA-add 4 columns
- Edit: [tools/tools_memory.py:476](../tools/tools_memory.py#L476) `memory_write` — compute scores at write time
- Edit: [tools/tools_memory.py:395](../tools/tools_memory.py#L395) `_hybrid_search_l3` — use salience composite
- New: [memory/salience.py](../memory/salience.py) — pure scoring functions
- New: [testing/test_salience.py](../testing/test_salience.py)

**Invariants at risk.**
- **Inv. 8** (L3 schema) — 4 new columns via PRAGMA pattern. Idempotent.
- Existing rows have NULL scores → default to `importance/10` for backward compat in queries.
- **Inv. 6** (distillation fallback) — heuristic extractor can't compute salience scores; default `surprise=0.5, goal_alignment=0, recency=1.0`.

**Mitigations.**
- Salience composite has explicit weights — A/B testable. Easy to revert to pure `importance` via env flag.
- Surprise computation is one cosine call per write → bounded.

**Tests.**
- Write two same-category facts; first has `surprise=high`, second has `surprise=low`.
- Goal alignment boosts retrieval for in-scope facts.
- NULL-score rows retrieve at expected baseline.

**Effort:** 2 days.

---

#### Upgrade 18 — Ebbinghaus forgetting curve (T-135)

**Gap.** Pi has `last_accessed_at` (T-082) but doesn't use it for decay. A fact untouched for 60 days ranks the same as one accessed yesterday. Real memory decays exponentially unless re-activated.

**Build.**
- Add `decay_rate` field per fact (default 0.01/day — half-life ~70 days).
- Effective importance at query time: `importance · exp(-decay_rate · days_since_last_access)`.
- Accessing a fact (read OR L3 injection match) bumps `last_accessed_at` → resets the decay clock. Already partially implemented in [tools/tools_memory.py:1018](../tools/tools_memory.py#L1018) `_bump_access`.
- Facts decay BELOW their original importance, never above. Pinned facts (`importance=10`, set via `pi memory pin`) skip decay entirely.

**Pruning consequence:** facts whose effective importance falls below 1.0 are **archived** (status='archived'), not deleted. Recoverable on explicit search.

**Files.**
- Edit: [tools/tools_memory.py:156](../tools/tools_memory.py#L156) `_init_sqlite` — add `decay_rate REAL DEFAULT 0.01`, `pinned INTEGER DEFAULT 0`
- Edit: [tools/tools_memory.py:200](../tools/tools_memory.py#L200) `memory_read` — apply decay in ORDER BY
- Edit: [agent/retention.py](../agent/retention.py) — new policy `decay_archive` that moves below-threshold rows to archived
- Edit: [scripts/memory_cli.py](../scripts/memory_cli.py) (if shipped from U5) — add `pi memory pin <id>` to set `pinned=1`
- New: [testing/test_decay.py](../testing/test_decay.py)

**Invariants at risk.**
- **Inv. 8** (L3 schema) — 2 new columns, PRAGMA pattern.
- **Inv. 9** (turn log non-fatal) — decay computation in retrieval must never crash a query. Wrap in try/except; on failure fall back to raw importance.
- Risk: a fact decays out, user complains it was "forgotten." Mitigation: archived ≠ deleted; recoverable via `pi memory list --include-archived`.

**Mitigations.**
- Decay rate tunable per category (`session_history` decays fast; `permanent_profile` slow).
- Pinned facts immune.
- `force_keep=True` flag on memory_write disables decay for a specific row.

**Tests.**
- Fact with high importance, untouched 100 days → effective importance dropped.
- Same fact accessed yesterday → near-original importance.
- Pinned fact → no decay regardless of age.
- Decayed-out fact recoverable via `--include-archived`.

**Effort:** 1 day.

---

#### Upgrade 19 — Idle replay / sleep consolidation (T-136)

**Gap.** Pi consolidates only at session-end and every 10 turns (`_maybe_mid_session_distill`). Hippocampal replay during slow-wave sleep is what makes humans good at long-term pattern detection. Pi has no analogue.

**Build.**
- New daemon thread `agent/idle_replay.py::IdleReplayManager`.
- Triggers when no user input for **5 minutes** AND no other background work pending.
- Picks **3 random episodes** from L1 (`raw_wiki`) in the past 7 days.
- Re-runs distillation on each episode in isolation, comparing to existing L2:
  - If existing L2 row matches semantically (cosine > 0.85) → no-op (already distilled).
  - If new facts emerge → write to L2 with `source='replay'`, `affect_tag='consolidated'`.
- Looks for **cross-session patterns**: same entity appearing in 3+ episodes → spawn a meta-fact ("Ash mentions GNN 14 times across April") in L2 with category `pattern_observation`.
- Hard cap: 1 replay/hour, max 10/day. Stops when user input arrives.

**Files.**
- New: [agent/idle_replay.py](../agent/idle_replay.py)
- Edit: [pi_agent.py:107](../pi_agent.py#L107) `__init__` — instantiate manager, start daemon
- Edit: [pi_agent.py:404](../pi_agent.py#L404) `process_input` — `manager.notify_activity()` resets idle timer
- Reuse: [memory/pipeline.py:137](../memory/pipeline.py#L137) `distill_session(rows=...)` with custom row subset
- New: [testing/test_idle_replay.py](../testing/test_idle_replay.py)

**Invariants at risk.**
- **Inv. 4** (memory namespace privacy) — in god mode, replay reads from `god_memory.db` only. Never cross-stream.
- **Inv. 6** (distillation fallback) — same router → same fallback chain. Replay uses `tier='cheap'`.
- Risk: replay storms when many idle periods accumulate. Mitigation: hard 1/hour cap + per-day limit.
- Risk: replay drains TPD budget. Mitigation: skip replay if `router.tpd_budget_remaining() < 20%`.

**Mitigations.**
- Replay is **read-mostly**; only writes new L2 facts if they don't already exist (dedup before insert).
- All replay writes tagged `source='replay'` so audit can filter them out.
- Daemon thread checks `notify_activity()` flag every 5 seconds → instant pause on user input.

**Tests.**
- Idle 5min → replay fires.
- User input → replay halts within 5s.
- 11th replay in a day → skipped (cap).
- Replay writes go to L2 with `source='replay'`.
- TPD budget low → replay skipped.

**Effort:** 3 days.

---

#### Upgrade 20 — Context-cued recall (T-137)

**Gap.** Humans recall better in the same context (mood, location, mode) they encoded. Pi has the data (mode is logged per L1 row, scope is logged per L2 row after U10 ships) but doesn't use it for retrieval boosting.

**Build.**
On every `memory_read`:
1. Inspect current `agent.mode` + `agent.current_scope`.
2. Apply retrieval boost: `+0.15 cosine` for same-mode matches, `+0.20` for same-scope matches.
3. Compose with existing salience score.

This is just an **ORDER BY tweak**, no schema change beyond U10's `scope` column. Pi already logs mode at write time in metadata.

**Files.**
- Edit: [tools/tools_memory.py:200](../tools/tools_memory.py#L200) `memory_read` — accept optional `current_mode` + `current_scope` params
- Edit: [pi_agent.py](../pi_agent.py) — pass `self.mode` + `self.current_scope` to `memory_read` calls
- Edit: [tools/tools_memory.py:395](../tools/tools_memory.py#L395) `_hybrid_search_l3` — apply context boosts
- New: [testing/test_context_cued_recall.py](../testing/test_context_cued_recall.py)

**Invariants at risk.**
- Depends on U10 (scope) shipping first.
- **Inv. 4** (privacy) — never cross-context-leak; god mode never sees public-scope boosts (it can't see public rows anyway via namespace).

**Mitigations.**
- Boost is additive on a 0-1 scale; doesn't override semantic relevance for off-context-but-highly-relevant rows.
- Toggleable via env: `PI_CONTEXT_CUED_RECALL=on` (default on after testing).

**Tests.**
- Two facts, equal cosine, different mode → same-mode wins on retrieval.
- Off-context fact still surfaces when it's the only relevant match.
- god mode retrieves god facts only (existing namespace test already covers this).

**Effort:** 1 day.

---

#### Upgrade 16 — File-state tracking (read-before-edit guard)

**Gap.** Claude Code tracks files read this session; refuses to edit one you didn't read first. Prevents stale-context clobbers. Pi's `modify_file` will happily overwrite a file the agent never read.

**Build.**
- New `agent/file_state.py` tracking `session_files_read: Set[str]`.
- `tools/tools_execution.py::read_file` adds to set.
- `tools/tools_execution.py::modify_file` refuses (with helpful error pointing the LLM to read first) if path not in set, unless `--force` in input or env override.

**Files.**
- New: [agent/file_state.py](../agent/file_state.py)
- Edit: [tools/tools_execution.py](../tools/tools_execution.py) — `read_file` updates state, `modify_file` checks state
- Edit: existing tests for `modify_file` — add a "read first" precondition
- New: [testing/test_file_state.py](../testing/test_file_state.py)

**Invariants at risk.**
- Existing tests that modify_file without read_file may break. Audit + fix.
- `create_file` is exempt (creating, not modifying).

**Mitigations.**
- Behind `PI_FILE_STATE_GUARD=on` for one release cycle; flip to default-on after audit.
- Tracking is per-session, in-memory; resets on exit. No persistence overhead.

**Effort:** 1 day (plus audit time for existing tests).

---

## 5. Implementation order (dependency graph)

Suggested sequence over ~6 weeks:

### Week 1 — Tier 1 sprint (immediate visible wins)
- Day 1: U1 (memory toast + intent) + U4 (cost footer)
- Day 2: U2 (continuation banner) + U3 (status line)
- Day 3: U5 (memory CLI)
- Day 4–5: verify everything; manual smoke test in `pi.py` + Telegram; ship.

### Week 2 — Memory experience deepening
- Day 1–2: U7 (query-conditional injection) — depends on `memory/semantic_dedup` (already present).
- Day 3: U8 (memory provenance) — depends on U7 (uses same injection point).
- Day 4–5: U6 (visible todos) — independent.

### Week 3 — Workflow
- Day 1–2: U9 (plan mode).
- Day 3–5: U10 (scope) — schema change; do it carefully, run migrations.

### Week 4 — Hooks + ingest
- Day 1–3: U12 (hook system) — wires existing passive scripts.
- Day 4–5: U16 (file-state guard) — small but needs test audit.

### Week 5 — Document ingest
- U11 (NotebookLM-style ingest) — 3 days plus integration polish.

### Week 6 — MCP + subagents
- Day 1–4: U13 (MCP adapter) — biggest tool surface multiplier.
- Day 4–6: U14 (subagent) — uses MCP for delegated work.

### Backlog (don't schedule yet)
- U15 (artifacts) — UX nicety; do when frequently writing > 50-line outputs becomes painful.

### Dependency map

```
U1 (toast) ──┐
U2 (cont)    │── independent, all Tier 1
U3 (status)  │
U4 (cost)    │
U5 (CLI) ────┘

U7 (semantic prefetch) → U8 (provenance)
U6 (todos) → independent

U9 (plan mode) → independent
U10 (scope) → could integrate with U6 (scoped todos)

U12 (hooks) → integrates with U2 (continuation reads recent state)
U16 (file guard) → independent

U11 (ingest) → reuses U10's scope concept
U13 (MCP) → independent infrastructure
U14 (subagent) → benefits from U13 (subagents can use MCP tools)
U15 (artifacts) → independent
```

---

## 6. Verification protocol after each upgrade

After EVERY upgrade, before closing the ticket:

```bash
python scripts/verify.py        # must say PASS
python scripts/refresh_pi.py    # regenerates PI.md §4/§7/§8/§9
```

Then update:
- `CHECKPOINTS/current.md` — phase, what changed, next step
- `solutions/SOLUTIONS.jsonl` — append `{id: S-NNN, ticket: T-NNN, title, fix, files_changed}`
- Move ticket: `mv tickets/open/T-NNN-*.json tickets/closed/`

**Manual smoke tests** (do these after each Tier 1 ship, weekly after that):
1. `python pi.py` → normie mode → "remember I prefer X" → toast appears → exit → restart → "what do I prefer?" → Pi recalls.
2. `python pi.py` → root mode → ask a coding question → verify cost footer + status line appear → check `tickets/open/` for any new auto-filed tickets.
3. Telegram path: send a message → response arrives without status line (Telegram path doesn't paint it).
4. God mode (if entering): `god mode` → write a fact → verify NOTHING lands in Supabase (check `logs/dropped_turns.jsonl`, `data/pi.db`).

---

## 7. Risk register — things to watch across all upgrades

| Risk | Trigger | Detection | Mitigation |
|---|---|---|---|
| Privacy leak (god → public) | New mode added without `public_logging=False` | Manual review of every `MODE_CONFIGS` change | Add `test_mode_privacy_invariants.py` that asserts each god-namespace mode has `public_logging=False` |
| Cache invalidation cascade | Adding content to `warm` segment in `build_system_prompt_split` | Sudden cost increase per turn | New per-turn context goes in `dynamic`, never `warm` |
| L3 schema drift | New column added without PRAGMA idempotent ALTER | Crashes on existing DB | Test runs against a pre-populated DB; verify migrations don't fail twice |
| Tool registry collision | New tool name overlaps with MCP-registered tool | Import-time assert | MCP tools are prefixed `mcp_<server>_`; grep before adding |
| Hook timeout cascade | Slow hook blocks pre_exit | Exit hangs | All hooks subprocess + timeout; pre_* events have hard cap |
| Subagent recursion | Subagent spawns subagent | Cost explosion | Depth counter + cost cap |
| MCP server hang | Network MCP server slow | Tool call hangs entire turn | Per-call timeout + circuit breaker |
| Embed latency | Gemini slow on query-conditional inject | Turn latency spikes | 400ms timeout, fall through to existing path |
| Sprint runner picks new mode | Adding new mode without updating `SAFE_COMPONENTS` | Auto-implement attempts risky edits | Review `RISK_FLAGGED` + `SAFE_COMPONENTS` per new component |
| Vault sync overwrites manual edits | User edits vault/*.md directly | Edit lost on next sync | Vault README already warns; nothing to do beyond doc |

---

## 8. What's intentionally NOT in this plan

- **Voice (Phase 8)** — separate roadmap. Has its own tickets.
- **Distributed (Phase 9 — Discord, web UI)** — separate roadmap.
- **Multi-agent debate (Phase 10)** — separate roadmap; partially exists as `research_mode`.
- **Rewriting `pi_agent.py` for size** — it's 1325 lines, single class. Tempting but: invariants live there. Wait until Tier 1+2 ship, then re-evaluate.
- **Replacing Supabase with something self-hosted** — works fine; would require migrating L1/L2 schema + dedup paths.
- **Cloud LLM cost optimization beyond the existing router** — router with TPD-budget brownout already handles this well.

---

## 9. Closing notes for Sonnet

1. **Read [PI.md](../PI.md) first.** This doc complements it; doesn't replace it.
2. **Before each upgrade, read its full §4 entry + the file refs.** Don't skim.
3. **Write the test before the code.** Pi's `verify.py` is the safety net — feed it before relying on it.
4. **Don't batch upgrades into one commit.** Each is its own ticket → solution → close cycle. Smaller diffs = safer reverts.
5. **If you discover something this doc got wrong** (e.g. file refs drifted, an invariant moved) — append a `## §10 Corrections` section to this file with date + correction. Don't silently fix; track drift.
6. **God mode is structurally private.** If touching anything in `MODE_CONFIGS["god"]`, `_get_memory_for_config`, `tools/tools_memory.py:_NoopSupabase`, `scripts/sprint.py:GOD_FORBIDDEN_PATHS` — propose diff first.
7. **The hard preferences in [PI.md §1](../PI.md) override anything here.** This doc is the *what* and *how to plan*. PI.md is the *how to behave*.

---

*End of UPGRADE_PLAN.md — last hand-edit 2026-05-25.*
