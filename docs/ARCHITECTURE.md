# Pi — Architecture (canonical)

**Version:** 2.0 (merged)
**Date:** 2026-04-25
**Supersedes:** `ARCHITECTURE.md` (v1.0), `ARCHITECTURE_DIRECTION.md` (v1.0). Both archived to [docs/_archive/2026-04-25/](_archive/2026-04-25/).
**Authority:** This is the canonical architecture document. When this disagrees with code, code wins, and this file gets a correction.

---

## 0. Core Principle

Pi is **not a chatbot**. Pi is an evolving agent system built around a continuous engineering loop:

```
build → test → create/fix ticket → run → execute → inspect output → detect failure/weakness → build again
```

Every architectural decision must support this loop. If a new module doesn't fit, it needs to justify its existence.

A second, equally non-negotiable principle:

> **Intelligence in prompt, not code.** Claude (driven by [prompts/consciousness.txt](../prompts/consciousness.txt)) makes decisions. Tools execute actions. There are no hard-coded behaviour patterns or regex routers — except for the bare minimum needed to keep the runtime safe (mode switching, exit, performance command).

---

## 1. System Flow

```
User Input
    ↓
pi_agent.py :: PiAgent.process_input
    ↓
[mode-switch detection — loose matcher, see §6]
    ↓
consciousness.txt + L3 context → system prompt   ← [tools_memory.get_l3_context]
    ↓
Claude Sonnet 4.6 receives: system + history + user message + tools schema
    ↓
Claude decides: which tool(s) to use (if any) → returns stop_reason="tool_use"
    ↓
Tool loop ([pi_agent.py:454-482]) executes each tool, appends tool_result, calls Claude again
    ↓
Final response generated when stop_reason != "tool_use"
    ↓
evolution.log_interaction → logs/evolution.jsonl
    ↓
Response to user
```

The tool loop is the heart of root mode. It is the difference between "Claude says it stored something" and "the database has the entry."

---

## 2. File Responsibilities

The runtime entry point is `pi_agent.py`. Status flags below match [FILE_INVENTORY.md](../FILE_INVENTORY.md).

| File | Role | Status |
|---|---|---|
| [pi_agent.py](../pi_agent.py) | Agent entry point. ~810-line monolith covering init, prompt building, mode switching, root/normie response paths, tool dispatch, research-mode dispatch, session summary, monthly review, health check, exit handling. Phase 4 of [PI_MASTER_PROMPT.md](../PI_MASTER_PROMPT.md) splits this into `agent/`. | LIVE |
| [prompts/consciousness.txt](../prompts/consciousness.txt) | Pi's intelligence — the system prompt that drives every decision in root mode. | LIVE |
| [prompts/system.txt](../prompts/system.txt) | Base system prompt for Groq (normie mode) and the research-mode personas. Does not list tools. | LIVE |
| [tools/tools_memory.py](../tools/tools_memory.py) | `MemoryTools` — L3/L2/L1 read/write, `get_l3_context`, SQLite cache, Supabase sync, write verification. Owns the `l3_cache` SQLite table. | LIVE |
| [tools/tools_execution.py](../tools/tools_execution.py) | `ExecutionTools` — `execute_python`, `execute_bash`, `read_file`, `modify_file`, `create_file`, `list_files`. | LIVE |
| [evolution.py](../evolution.py) | `EvolutionTracker` (telemetry to `logs/evolution.jsonl` + per-pattern stats to `logs/patterns.jsonl`); `SelfModifier` (reserved — not yet wired). | LIVE |
| [core/research_mode.py](../core/research_mode.py) | 3-agent debate (Claude + Gemini + Groq), 2 rounds, synthesised verdict. Invoked via lazy import inside `process_input`. | LIVE |
| [app/config.py](../app/config.py) | `.env` loading, API keys, model strings, default mode, daily cost limit. | LIVE |
| [SUPABASE_SETUP.sql](../SUPABASE_SETUP.sql) | Authoritative Supabase schema: `l3_active_memory`, `organized_memory`, `raw_wiki`, RLS policies, Ash's permanent profile seed. | LIVE |
| [llm/routing.py](../llm/routing.py) | Old multi-provider routing layer. Not imported by anything. Will be archived in Phase 4. | DEAD (legacy) |
| [app/state.py](../app/state.py) | Old 10-table SQLite schema (users / devices / threads / messages / memories / documents / tool_runs / cost_log / settings / audit_logs). Not imported by anything; not used at runtime. Acknowledged in [data/README.md](../data/README.md#L35). Will be archived in Phase 4. | DEAD (legacy) |

---

## 3. Memory Architecture

### Three tiers

**L3 — Active Context (always loaded)**
- Storage: Supabase table `l3_active_memory` + SQLite cache `l3_cache`
- Size: ~800 token budget at injection time
- Purpose: Always-loaded context for the current session
- Sync: SQLite is wiped + repopulated from Supabase at startup, then again on a 5-minute TTL ([tools/tools_memory.py:302-305](../tools/tools_memory.py#L302-L305))

**L2 — Organized Memory**
- Storage: Supabase table `organized_memory`
- Size: unlimited
- Purpose: Searchable structured knowledge — preferences, decisions, technical notes
- Categories: dynamic (anything writes can produce; no hardcoded list, see L-005)

**L1 — Raw Archive**
- Storage: Supabase table `raw_wiki`
- Size: rolling 30-day window (planned; not yet enforced)
- Purpose: Complete interaction history, indexable for replay
- Threading: every L1 write in a session shares the same `thread_id = session_id` (T-013, [tools/tools_memory.py:223-228](../tools/tools_memory.py#L223-L228))

### Memory invariants (do not break)

These were learned the hard way (see L-005, L-006, L-007, L-008, L-010 in [solutions/LESSONS.md](../solutions/LESSONS.md)):

1. **Write path and read path must be tested together.** A memory entry is only real if `get_l3_context()` after writing returns it. Round-trip tests, not unit tests.
2. **`verified=True` means durable, not cached.** `_verify_write` checks Supabase (the durable store), not just SQLite (the cache). SQLite is wiped on every `_sync_l3()`. ([tools/tools_memory.py:401-422](../tools/tools_memory.py#L401-L422))
3. **No hardcoded category lists in read paths.** `get_l3_context()` groups dynamically by whatever categories the writes produced. ([tools/tools_memory.py:329-370](../tools/tools_memory.py#L329-L370))
4. **`_sync_l3` is expensive — TTL it.** Full Supabase fetch + SQLite wipe + reinsert. Minimum 300s between syncs. ([tools/tools_memory.py:26-27, 302-305](../tools/tools_memory.py#L26-L27))
5. **Session IDs propagate everywhere.** Generated once per startup ([pi_agent.py:68](../pi_agent.py#L68)). Lands in evolution log metadata, in L1 raw_wiki `thread_id`, in session summaries. Without it, logs are a pile of disconnected events.

### Known limitations (not yet fixed)

- **L2 search filters on title only**, not content. L2 stores full content under `content.text` (JSONB) but the search uses `ilike("title", ...)`. Plan: add a content-side filter and merge results, or full-text-index the JSONB. Tracked: [SCHEMA_MISMATCHES.md SM-003](../SCHEMA_MISMATCHES.md), Phase 3 of [PI_MASTER_PROMPT.md](../PI_MASTER_PROMPT.md).
- **`memory_read(tier=None)` excludes L1** despite the docstring claiming "None for all". Tracked: open ticket T-017, [SCHEMA_MISMATCHES.md SM-004](../SCHEMA_MISMATCHES.md).
- **Token budget competition.** `get_l3_context()` shares 800 tokens across all categories. `session_history` (importance=4) competes with `permanent_profile` (importance=10) and frequently loses. Plan: per-category token reservations.
- **No deduplication on L3 write.** Repeated writes accumulate.
- **No importance decay.** Old high-importance entries crowd out newer lower-importance ones.
- **L1 auto-logging not implemented.** Every conversation turn should append to L1, but currently only explicit `memory_write(tier="l1")` calls populate `raw_wiki`.

---

## 4. Tool System

Eight tools, defined at [pi_agent.py:140-238](../pi_agent.py#L140-L238). Available **only in root mode** (Claude). Normie mode (Groq) has zero tools by design — see §6.

| Tool | Purpose | Notes |
|---|---|---|
| `memory_read(query, tier?)` | Search memory across L3/L2 (and L1 if explicit) | See SM-004 limitations |
| `memory_write(content, tier, importance, category, expiry?)` | Store; auto-verifies in both stores | tier defaults to l3 |
| `memory_delete(target, soft)` | Soft-delete archives to L2; hard-delete removes | Default soft=True |
| `execute_python(code)` | Run Python in a 30s-timeout subprocess | Output captured |
| `execute_bash(command)` | Run shell command with 30s timeout | Working dir = project root |
| `read_file(path, lines?)` | Read file, optional line range | Absolute or repo-relative |
| `modify_file(path, old_str, new_str)` | String-replace; requires `old_str` to be unique in the file | Includes auto-log to L3 |
| `create_file(path, content)` | Create new file; verifies existence after write | Includes auto-log to L3 |

### Standard tool result shape

```json
{"success": true | false, "output"?: "...", "error"?: "...", "verified"?: true | false}
```

The agent loop ([pi_agent.py:454-482](../pi_agent.py#L454-L482)) appends each result as a `tool_result` block keyed by `tool_use_id`, then calls Claude again with the updated message list. The loop continues while `stop_reason == "tool_use"`.

### Truncation safety

`self.messages` is bounded to 20 entries via `_truncate_messages_safely` ([pi_agent.py:509-520](../pi_agent.py#L509-L520)). This walks forward from the naive slice point until it lands on a plain user-text message — never cutting inside a `tool_use` / `tool_result` pair. (See L-007.)

---

## 5. Mode System

| Mode | Model | Tools | Cost | Use case |
|---|---|---|---|---|
| **Normie** | Groq Llama 3.3 70B | None | $0 | Casual chat, quick questions |
| **Root** | Claude Sonnet 4.6 | All 8 | ~$0.003/msg | Memory ops, code execution, complex tasks |
| **Research** | Claude + Gemini + Groq | None (debate orchestration) | ~$0.02/2-round debate | Multi-perspective analysis |

### Mode switching

`process_input` ([pi_agent.py:344-371](../pi_agent.py#L344-L371)) uses a loose matcher (S-010, T-015):
- Short messages (≤8 words after stripping punctuation) containing `root`/`normie`
- Plus a switch signal: the literal word `mode`, a switch verb (`switch`, `go`, `enter`, `activate`, `use`, `into`, `to`, `now`), or just the mode name on its own
- Strict commands (`analyze performance`, `research mode`, `exit`) stay strict

**Why loose matching matters:** when the matcher missed natural variants, the LLM didn't refuse — it *mimed* the missing capability (fake banners, fake "type confirm" prompts), leaving the user stranded in the wrong mode. The fix is documented in L-009.

### Cross-mode continuity

Both response paths write to a single canonical store, `self.messages` ([pi_agent.py:434, 448, 552, 568](../pi_agent.py#L434)). Normie used to write only to `self.history`, leaving Claude blind to normie turns after a switch back to root. That bug (T-016 / S-011) is fixed; the rule is in L-010: **one conversation, one store**.

### Daily cost gate

If `evolution.get_daily_cost() >= DAILY_COST_LIMIT` ($0.50 default), the agent auto-switches root → normie ([pi_agent.py:402-406](../pi_agent.py#L402-L406)).

---

## 6. Engineering Loop

Pi is a system that learns from its own failures. Every component contributes to the loop:

### 6.1 Logging pipeline ([logs/](../logs/))

- `logs/evolution.jsonl` — one JSONL line per interaction. Fields: timestamp, mode, model, success, cost, tokens_in/out, `tools_used` (list of names), `metadata.session_id`. Owned by [evolution.py:25-55](../evolution.py#L25-L55).
- `logs/patterns.jsonl` — per-tool success and duration, aggregated by `track_pattern`.
- `logs/last_review.json` — monthly review marker (last completed / last declined timestamps).

**Known schema drift:** `evolution.log_interaction` writes `tools_used` (name strings) but `evolution.analyze_performance` reads `tool_calls` (full call objects) — that field is never written. Result: every analytic about tool usage and tool success is silently empty. Tracked: [SCHEMA_MISMATCHES.md SM-001](../SCHEMA_MISMATCHES.md). Phase 2 of [PI_MASTER_PROMPT.md](../PI_MASTER_PROMPT.md) fixes it.

### 6.2 Ticket pipeline ([tickets/](../tickets/))

`tickets/open/`, `tickets/closed/`. Schema (every ticket has all of these):

```json
{
  "id": "T-NNN",
  "title": "short verb-led description",
  "component": "file:function",
  "what_failed": "...",
  "where_failed": "file + function + line",
  "why_likely": "one-line hypothesis",
  "severity": "P0|P1|P2|P3",
  "reproduction": "exact steps",
  "expected": "...",
  "actual": "...",
  "suggested_fix": "...",
  "status": "open|in_progress|closed|blocked",
  "created": "ISO timestamp",
  "closed": "ISO timestamp or null",
  "linked_solution": "S-XXX or null"
}
```

11 tickets are currently closed (T-006 through T-016). Open tickets at the time of this writing live in [analysis/tickets.jsonl](../analysis/tickets.jsonl) — T-017, T-018, T-019 — pending promotion to `tickets/open/`.

### 6.3 Solution / lesson pipeline ([solutions/](../solutions/))

Every fix produces a row in `solutions/SOLUTIONS.jsonl` (S-NNN). Recurring patterns produce a lesson in `solutions/LESSONS.md` (L-NNN). These are **the engineering memory of the project** — they are what eventually let Pi (or future-Ash) say "we've seen this kind of failure before; here's the rule we extracted."

L-001 through L-010 are written. Read [solutions/LESSONS.md](../solutions/LESSONS.md) before touching shared state or session logic.

### 6.4 Conversation analysis pipeline ([analysis/](../analysis/))

Real conversations are the highest-signal failure source: tests catch known bugs, logs catch crashes, but conversations catch *silent* failures — weak answers, lost continuity, hallucinated tool effects. The pipeline is documented in [analysis/WORKFLOW.md](../analysis/WORKFLOW.md).

```
Ash pastes a conversation into analysis/chat_logs.txt
  ↓
Claude scans for failure signals (memory, continuity, hallucination, drift, tool misuse, refusals)
  ↓
Each finding → ticket in analysis/tickets.jsonl  (T-015+)
  ↓
Validated tickets promote to tickets/open/
  ↓
Recurring patterns (≥ 2 sessions) tracked in analysis/SUMMARY.md
  ↓
Solutions land in solutions/SOLUTIONS.jsonl
  ↓
Recurring patterns get a lesson in solutions/LESSONS.md
```

`analysis/chat_logs.txt` is gitignored (raw conversations are personal). Tickets in `analysis/tickets.jsonl` are public — personal content is scrubbed per the privacy rule in [analysis/WORKFLOW.md](../analysis/WORKFLOW.md).

### 6.5 Health & diagnostics

`_health_check()` ([pi_agent.py:629-655](../pi_agent.py#L629-L655)) runs at startup: Supabase reachability, SQLite reachability, presence of all 3 API keys. The monthly review check ([pi_agent.py:657-714](../pi_agent.py#L657-L714)) prompts Ash if 30+ days have passed since the last review.

---

## 7. Long-term autonomy plan

Master prompt §1.10 calls `analysis/` "the most honest source of truth in the repo." That stays true because of one rule: **observe first, act later.**

| Phase | Pi's autonomy level |
|---|---|
| **A — now** | Pi generates great logs. Ash reads them and acts. |
| **B — next** | Pi reads its own logs and proposes tickets and fixes. Ash approves. |
| **C — later** | Pi executes approved fixes within a bounded scope. Ash reviews. |
| **D — long-term** | Pi proposes architectural changes. Ash decides. |

At no point does Pi modify its own identity (`consciousness.txt`) or core files without review. That's a design choice, not a technical limit. An autonomous agent that *earns* autonomy through track record is more interesting than one given it.

### Things that must be preserved permanently

These records are Pi's engineering biography:

- All session traces (`logs/runs/{session_id}.jsonl` once implemented; for now `logs/evolution.jsonl`)
- All `tickets/` (open and closed)
- All `solutions/SOLUTIONS.jsonl` entries
- Every version of `prompts/consciousness.txt` (versioned)
- All `analysis/chat_logs.txt` (locally — gitignored, but never wiped)
- Git history of `pi_agent.py` and `tools/`

---

## 8. Design Constraint — "No Ceiling"

Don't build Pi in a way that requires a rewrite to grow. Every module should be:

- **Replaceable** — swap Groq for another fallback without touching `pi_agent.py`.
- **Extendable** — add a new tool without changing the tool loop, just `_get_tool_definitions()` and the dispatch in `_execute_tool`.
- **Observable** — every component writes to logs.
- **Testable** — every component has, or can have, a test file. (See §11 — currently incomplete.)

The architecture you build today will be running for years. Design like it.

---

## 9. Build status (as of this document's date)

The honest as-of-2026-04-25 picture is in [STATUS.md](../STATUS.md). Headline: tools wired, tool loop working, session_id propagating, telemetry has a silent drift (SM-001), memory round-trip via the tool loop is unverified by tests. See STATUS.md for citations.

---

## 10. What this document does NOT cover

- Step-by-step "how to run Pi" — see [docs/USER_GUIDE.md](USER_GUIDE.md).
- Per-bug histories — see [tickets/closed/](../tickets/closed/) and [solutions/SOLUTIONS.jsonl](../solutions/SOLUTIONS.jsonl).
- Operating protocol for VS Code Claude during engineering work — see [PI_MASTER_PROMPT.md](../PI_MASTER_PROMPT.md).
- Future engineering protocol / contributing guide — Phase 6 will produce [docs/CONTRIBUTING.md](CONTRIBUTING.md).

## 11. Current testing gaps (worth naming explicitly)

The repo has 18 tests across 4 suites in [testing/](../testing/), but **none of them invoke `PiAgent.process_input`**. They call `MemoryTools` directly. The thing that broke in production (LOG1/LOG2 — the LLM saying "I've stored…" without a tool call) is exactly the gap. Phase 3 of [PI_MASTER_PROMPT.md](../PI_MASTER_PROMPT.md) is dedicated to closing it: a `testing/test_memory_roundtrip.py` that drives `PiAgent`, asserts a real `memory_write` tool_use was issued, tears down, rebuilds, queries, and asserts the answer comes back.
