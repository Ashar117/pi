# Pi — Architecture (canonical)

**Version:** 4.0
**Date:** 2026-07-07
**Authority:** This is the canonical architecture document. When this disagrees with code, code wins, and this file gets a correction.
**Prior versions:** v3 (Phase 9 delta over v2) archived at [docs/_archive/2026-07-07/ARCHITECTURE.v3.md](_archive/2026-07-07/ARCHITECTURE.v3.md). v4 is a clean rewrite — v3's body described the April 2026 system (8 tools, no registry, old ticket schema) and had become actively misleading.

---

## 0. Core principles

Pi is **not a chatbot**. Pi is an evolving agent system built around a continuous engineering loop:

```
build → test → ticket → run → inspect output → detect failure/weakness → build again
```

Every architectural decision must support this loop. Two further non-negotiables:

1. **Intelligence in prompt, not code.** Claude (driven by [prompts/consciousness.txt](../prompts/consciousness.txt)) makes decisions; tools execute actions. Hard-coded routing exists only where runtime safety demands it (mode switching, exit, special commands).
2. **Honesty over narrative.** Claims live in [ABOUT.md](../ABOUT.md) with ✅/◐ status; anything unproven stays ◐ no matter how finished the code looks. The recurring failure mode of this project is docs claiming what code doesn't do — when in doubt, trust auto-generated docs (PI.md auto sections, docs/STATUS.md, prompts/capabilities.md) over hand-written ones.

---

## 1. One turn, traced (root mode)

```
process_input → _process_input_inner
  → detect mode switch / special commands → budget check
  → _respond_via_config(cfg):
      fetch L3 memory → filter tools by ModeConfig → awareness shortcut
      → split system prompt (static / warm / dynamic — 3-segment Anthropic prompt cache, R10)
      → router.chat()
      → while stop_reason == "tool_use": execute tool → append tool_result → router.chat()
  → finalize → async log (L1 raw_wiki + evolution.jsonl) + durable logs/turns.jsonl
```

The tool loop is the heart of root mode: it is the difference between "Claude says it stored something" and "the database has the entry."

Every surface (terminal REPL, brain server, web UI, extension, Telegram) enters through the same `process_input` — there is exactly one turn path.

---

## 2. Modes — config, not code paths

One `ModeConfig` dataclass ([agent/modes.py](../agent/modes.py)) drives all response paths through the single `_respond_via_config` method (ADR-001, [ADR-004](adr/004-modeconfig-unifies-response-paths.md)). There is no per-mode branching inside the turn path.

| Mode | Model | Tools | Notes |
|---|---|---|---|
| root | Claude Sonnet 4.6 | all (~75 — live count in PI.md §7) | default working mode; file edits |
| normie | Groq Llama 3.3 70B | minimal allowlist | free, fast chat |
| research | Claude + Groq + Gemini | debate orchestration | 2 rounds + synthesis; also callable from root via `deep_debate` (T-262) |

`ModeConfig.tool_allowlist` semantics: `None` = all tools, `()` = none, non-empty tuple = explicit whitelist.

Mode switching is a loose matcher in `agent/modes.py::detect_mode_switch` — deliberately forgiving, because a missed switch historically made the LLM *mime* the other mode instead of refusing (L-009).

---

## 3. Tool system — registry, not dispatch ladder

Each tool is a `ToolSpec` declared in its owning `tools/tools_*.py` module's `TOOLS = [...]` list and registered through [agent/tools.py](../agent/tools.py) ([ADR-002](adr/002-tool-registry-pattern.md)). Adding a tool = one list entry in the owning module. There is **no central elif ladder** — do not look for one, do not create one.

- 21 tool modules; ~75 tools. The live inventory is auto-generated into PI.md §7 and [prompts/capabilities.md](../prompts/capabilities.md).
- Standard result shape: `{"success": true|false, "output"?, "error"?, "verified"?}`.
- Safety contracts encoded in tools, not prompts: `gmail_send` **creates drafts only** — it cannot send (T-271). Watcher/triage flows inherit human-in-the-loop from this.
- Message history is bounded with pair-safe truncation (never cuts inside a `tool_use`/`tool_result` pair — L-007; logic in [agent/truncation.py](../agent/truncation.py)).

---

## 4. Memory — three tiers

| Tier | Store | Contents | Access |
|---|---|---|---|
| L1 | `raw_wiki` (Supabase) | every turn, all modes; auto-logged | pruned ~30 days; offline floor is `logs/turns.jsonl` (+ `logs/dropped_turns.jsonl`) |
| L2 | `organized_memory` (Supabase) | distilled durable facts; Groq writes at session end | on-call via `memory_read`; lexical + embedding dedup chain on write |
| L3 | `l3_cache` (SQLite) ← synced from `l3_active_memory` (Supabase) | hot ambient context | injected into system prompt every turn, `get_l3_context(max_tokens=800)` |

**L3 sync mechanics** ([tools/tools_memory.py](../tools/tools_memory.py) `_sync_l3`): rows fetched from Supabase are UPSERTed into SQLite (T-306) — only the 7 columns Supabase's `l3_active_memory` owns get overwritten, so local-only columns (embedding, decay_rate, pinned, mode, conversation_id, scope, `kind='derived'` rows, ...) survive every sync instead of being reset to defaults by a blanket delete-and-reinsert. Ordered `created_at desc`, capped at 5,000 rows, minimum 300s between syncs. The ordering+cap is T-270 — an unordered/uncapped select silently dropped the *newest* rows once the table grew past the server's default page size.

**Retrieval (T-292/T-293):** `MemoryTools.retrieve(query, k, tiers)` fuses dense cosine similarity (query + stored embeddings — Qwen `text-embedding-v4` when `QWEN_API_KEY` is set, Gemini otherwise) with the existing lexical ranking (`_hybrid_search_l3` BM25, `memory_read` for L2), min-max normalized and weighted (`PI_RETRIEVE_W_DENSE`/`W_LEX`/`W_IMPORTANCE`). Degrades to pure lexical ordering with no embedding provider configured. This replaced `_prefetch_memory`'s single-keyword-extraction lookup — the old path missed any paraphrase with zero lexical overlap with the stored fact. L3 rows carry an `embedding` column (T-291), filled by `backfill_l3_embeddings()` at session exit rather than inline on write (keeps the interactive write path fast).

**Forgetting is a four-mechanism lifecycle, not a TTL:**

1. **Scheduled expiry** (`active_until`) — explicit (`memory_write(expiry=...)`) or auto-inferred from ephemeral phrasing ("just for today", "until friday", "for the next N days" — T-299's `_infer_expiry`). Pruned by the daily retention tick.
2. **Neglect decay** (T-135/T-300) — `effective_importance = importance * exp(-decay_rate * days_since_access)`, per-category rates ([memory/salience.py](../memory/salience.py)); unpinned rows below threshold move to `l3_archive` daily (`PI_DECAY_ARCHIVE`, default on) via [memory/archive.py](../memory/archive.py)'s `archive_l3_row` (T-309) — a real table move, not just an in-place flag, so nothing in any production read path (retrieve, BM25, dedup, contradiction scan) can ever surface it again. Access reinforces (`_bump_access` resets the clock), so used memories survive and unused ones fade.
3. **Contradiction** — lexical topic-key grouping (`scan_contradictions`) catches same-topic conflicts; Qwen-adjudicated `scan_semantic_contradictions` (T-303, tier='cheap', cosine-prefiltered over stored embeddings, capped at `PI_CURATE_MAX_CALLS`) catches implication-level ones the lexical scan structurally can't ("moved to Boston" vs "apartment in Atlanta" share no topic key). Both soft-invalidate (`invalid_at`) — never delete; a superseded fact stays queryable for "what did I tell you before."
4. **Semantic dedup** (T-080) — merges paraphrase duplicates at session exit; loser gets `superseded_by`.

All four are soft/recoverable and visible in one place: `python scripts/memory_cli.py forgotten [--days N]` (T-301) or the `/memory` dashboard's forgetting ledger panel — classified EXPIRED/DECAYED/CONTRADICTED/MERGED by deterministic precedence (DECAYED added T-309, distinct from EXPIRED: forgotten for being unused, not for a timed-out `active_until`). `memory_delete`/`memory_cli forget` also route non-ID targets through `retrieve()` (T-302), so "forget everything about my old internship" finds a fact phrased with zero shared words.

**Memory invariants (learned the hard way — do not break):**

1. **Write path and read path are tested together.** An entry is only real if the read path returns it after the write path stored it. Round-trip tests, not unit tests. This project's #1 recurring bug class is write/read divergence (L-005…L-010, T-270).
2. **`verified=True` means the write actually landed where it was supposed to** — SQLite always, plus Supabase when it's configured (T-309: previously required a Supabase ack even in offline/noop mode, so every L3 write reported `success=False` on any Supabase-less checkout despite the SQLite write succeeding). SQLite rows survive syncs now (T-306 UPSERT) rather than being wiped.
3. **No hardcoded category lists in read paths** — `get_l3_context` groups by whatever categories writes produced.
4. **`_sync_l3` is expensive — TTL it** (300s minimum between syncs).
5. **Session IDs propagate everywhere** (evolution log, L1 `thread_id`, session summaries) or the logs are a pile of disconnected events.

**Caretaker layer** (Phase 8.8): bubble collector debounces rapid incoming messages into one atomic turn; memory caretaker reconciles contradictory/stale facts instead of appending forever; providers fail through explicit fallback chains ([ADR-007](adr/007-memory-lifecycle.md)).

---

## 5. Conversation persistence + episodic recall

SQLite tables `conversations` + `conversation_turns` (schema in `MemoryTools._init_sqlite`; idempotent `INSERT OR IGNORE` on `(conversation_id, idx)`).

- Key methods (all [tools/tools_memory.py](../tools/tools_memory.py)): `create_conversation`, `persist_turn`, `load_conversation_turns`, `list_conversations`, `title_conversation`, `close_conversation(digest)`, `recall_episode(query)`.
- `conversation_switch(agent, target_conv_id)` ([agent/conversation.py](../agent/conversation.py)) saves/restores `agent.conversation_id` + `agent.messages` around autonomous turns — Telegram/brain-server/watcher turns never splice into the active terminal thread.
- REPL commands: `chats`, `resume <id>`, `/newchat`.
- Storage transport sits behind the `StorageBackend` seam ([agent/storage.py](../agent/storage.py)): `SQLiteStorageBackend` (prod) / `InMemoryStorageBackend` (tests). Phase 2 of that migration (L3 core read/write behind the seam) is intentional piggyback-debt — see ticket T-269.

---

## 6. Network layer

```
Chrome MV3 extension (extension/)  ─┐ HTTP + SSE
Web chat UI (web/)                 ─┤──→ app/server.py (FastAPI, 127.0.0.1:7712) ──→ process_input()
Telegram peer (tools/tools_telegram.py) ──────────────────────────────────────────↗
```

- **Brain server** ([app/server.py](../app/server.py), started by [pi_daemon.py](../pi_daemon.py)): localhost-only by design; Bearer auth via `hmac.compare_digest`; one turn at a time (module-level `asyncio.Lock`, FIFO); routes `GET /` (web UI), `/health`, `/conversations`, `POST /chat`, `GET /chat/stream` (SSE); CORS for `chrome-extension://` and `http://127.0.0.1:*`.
- **Web UI** ([web/](../web/)): no framework, no build step; shared `web/chat.js` client library; SSE token streaming.
- **Extension** ([extension/](../extension/)): side panel reusing `chat.js`; "Ask Pi about this page" context menu captures `{selection, url, title}`; conversation `extension:default`.
- **Telegram peer**: each chat isolated as `telegram:<chat_id>`; all dispatch paths (bubble, media, voice, text, callback buttons) funnel through `_process_as_telegram_peer`. Inline keyboards: `send_buttons()`; button taps route through `handle_callback` into a normal turn (T-220), with the `emailtriage:` prefix mapping taps to Gmail/Calendar instructions (T-258).

---

## 7. Background machinery

- **Watchers** ([agent/watchers.py](../agent/watchers.py)): evaluator functions `(config, state) -> (fired, detail, new_state)` registered in `_EVALUATORS` — file / schedule / url / keyword / price / **email** (T-257: `gmail_search("is:unread newer_than:1d")`, seen-id diffing). 60s sweep. Alerts go to Telegram via the functions `pi_agent.py` wires in: `TelegramTools.send` and `TelegramTools.send_buttons` — **the attribute is `send`, not `send_message`**; getting that wrong silently killed all watcher alerts for months (T-274). Email alerts get triage buttons; everything else gets plain sends. `analyze=True` routes an event through a dedicated Pi conversation (6/hour cap).
- **Scheduler** ([tools/tools_scheduler.py](../tools/tools_scheduler.py)): cron-style jobs inside the daemon, including nightly `turns.jsonl` rotation at 03:30 (50MB threshold → gzip to `logs/archive/`, T-259; standalone fallback: `scripts/passive/turns_log_rotate.py`).
- **Observability** ([agent/observability.py](../agent/observability.py)): `track_silent(category, exc)` writes swallowed exceptions to `data/silent_failures.db` and never raises. Bare `except:` anywhere in the repo is a verify.py **FAIL** (AST lint, T-273). P1-class categories push one throttled, deduped Telegram alert per day ([scripts/passive/silent_failure_watcher.py](../scripts/passive/silent_failure_watcher.py), T-265). Exception-handling policy: swallowing is allowed only with `track_silent`; round 2 of the cleanup (T-264) is deliberately data-driven — fix the observed top offenders, not a blind mega-audit.

---

## 8. Engineering loop (the part that genuinely works)

```
ticket → reproducing test → fix → python scripts/verify.py (PASS)
  → append solutions/SOLUTIONS.jsonl → move ticket open/ → closed/ → python scripts/refresh_pi.py
```

- **Tickets** ([tickets/open/](../tickets/open/), [tickets/closed/](../tickets/closed/)) — JSON, one per file. Current schema: `id, title, component, severity(P0–P3), source, current_state, target_state, depends_on[], status, created, migration_plan[], risk_notes`, optional `root_cause_confidence("verified"|"hypothesis")` (the sprint runner's T-154 gate reads it), and on close: `resolution, solution_id, closed`. A ticket must be executable by a cold session: file:line evidence in `current_state`, concrete steps in `migration_plan`, the proving test named.
- **Solutions** ([solutions/SOLUTIONS.jsonl](../solutions/SOLUTIONS.jsonl)) — append-only, one JSON per line: `id, ticket_ids[], problem, countermeasure, files_changed[], result, lessons[], date, recurring`. Recurring patterns distill into [solutions/LESSONS.md](../solutions/LESSONS.md) (L-NNN).
- **verify.py** ([scripts/verify.py](../scripts/verify.py)) — the gate: AST syntax check on every `.py`, bare-`except:` lint (FAIL), keystone coherence tests first, then every non-costly test file as its own pytest subprocess; WARN-only checks for test coverage, consciousness↔tool drift, replication-log divergence, ABOUT.md count drift. Writes [docs/STATUS.md](STATUS.md). **Never pipe or chain its invocation** — the pipe returns the last command's exit code and masks FAIL (T-214); read the printed PASS/FAIL. Costly tests (real API hits) are excluded via the `COSTLY_TESTS` set and live in [docs/LIVE_RETEST_CHECKLIST.md](LIVE_RETEST_CHECKLIST.md).
- **Passive skills** ([scripts/passive/](../scripts/passive/)) — 13 read-only health checks (doc drift, privacy publish guard, silent failures, memory pollution, sprint readiness, …) writing to [reports/](../reports/); never auto-fix, never commit. Exit codes 0/1/2 = PASS/WARN/FAIL.
- **Cadence**: `plan_sprint.py` (Monday, writes PI.md §3) · `retro.py` (Friday) · `refresh_pi.py` (after any ticket close or tool addition — regenerates PI.md §4/§7/§8/§9).

---

## 9. Autonomy — sprint runner ([scripts/sprint.py](../scripts/sprint.py))

Feature-complete, heavily gated, and — honestly — **has never closed a ticket in production** (T-256 tracks the first real close; it is blocked on a genuine candidate ticket, not on code).

Guardrails, all load-bearing:

- `SAFE_COMPONENTS` allowlist (`scripts/`, `testing/`, `docs/`, `vault/`) — ~80% of the repo is refused; scope is widened one prefix per proven run (T-263), never preemptively.
- T-154 confidence gate: self-filed tickets default to `hypothesis` and are refused for auto-runs; only `root_cause_confidence: "verified"` tickets qualify. `--ticket T-NNN` is the by-design manual bypass.
- Caps: 15 min / 30 iterations / $0.50 per ticket. Commits only to `sprint/T-NNN` branches; Ash gates every merge. Failures escalate via Telegram, and the run transcript in `logs/sprint/` is itself the deliverable.

The autonomy ladder is deliberate: **A** Pi generates great logs → **B** Pi proposes tickets and fixes (current) → **C** Pi executes within bounded scope (T-256 proves this) → **D** Pi proposes architectural change. Autonomy is earned through track record, never granted by config.

---

## 10. Design constraints

- **No ceiling**: every module replaceable, extendable (registry, ModeConfig, StorageBackend seam), observable (everything logs), testable.
- **Reversibility**: files are never deleted — archive to [docs/_archive/](_archive/). Identity ([prompts/consciousness.txt](../prompts/consciousness.txt)) and core files change only through diff-first review.
- **Cost discipline**: [core/llm_router.py](../core/llm_router.py) picks provider/tier with per-provider tokens-per-day budgets and brownout ([ADR-003](adr/003-router-tier-and-tpd-budget.md)); daily cap in `app/config.py` auto-downgrades root → normie. Groq (free) for batch/aggregation; Claude for code precision.
- **Privacy**: the public repo model separates open code from private prompts/architecture (gitignored). It is enforced by `/privacy` ([scripts/passive/privacy_publish_guard.py](../scripts/passive/privacy_publish_guard.py)) before any push.

---

## 11. Where to look next

| Question | Doc |
|---|---|
| How do I run it / what commands exist | [docs/USER_GUIDE.md](USER_GUIDE.md) |
| What works vs what's ◐ | [ABOUT.md](../ABOUT.md) |
| What's in flight right now | [PI.md](../PI.md) §3/§8 (auto-refreshed) |
| Why is X designed this way | [docs/adr/](adr/) 001–008 |
| What broke before and what we learned | [solutions/SOLUTIONS.jsonl](../solutions/SOLUTIONS.jsonl), [solutions/LESSONS.md](../solutions/LESSONS.md) |
| Last verify result | [docs/STATUS.md](STATUS.md) (machine-written — trust it) |
