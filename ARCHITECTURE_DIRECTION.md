# Pi — Architectural Direction & Engineering Loop Design

**Version:** 1.0  
**Date:** 2026-04-20  
**Author:** Ash  
**Status:** Canonical — applies to all future development

---

## Core Principle

Pi is NOT a chatbot. Pi is an evolving agent system built around a continuous engineering loop:

```
build → test → create/fix ticket → run → execute → inspect output → detect failure/weakness → build again
```

Every architectural decision must support this loop. If a new module doesn't fit cleanly into this loop, it needs to justify its existence.

---

## Required Architecture: What To Build

### 1. Full Logging Pipeline

**Location:** `logs/`  
**Files:** `runs/`, `outputs/`, `errors/`, `events/`

**What it stores:**
- Every agent run: start time, end time, mode, model used, cost, user input, response
- All tool calls and results
- All errors with full tracebacks
- All interruptions and stop conditions
- Flagged weak/bad outputs (not just crashes — mediocre is also worth tracking)
- System events: mode switches, memory syncs, health check results

**Design rule:** Logs must be useful for debugging and improvement. No noise-only entries. Every log line should be answerable to: "could this help diagnose a failure or improve behavior?"

**Format:** JSONL per session, indexed by session ID. Raw enough to replay, clean enough to query.

**Build now:** Basic JSONL logging (already exists in evolution.py). Expand to capture outputs and flagged responses.

---

### 2. Full Ticketing / Problem Tracking Pipeline

**Location:** `tickets/`  
**Files:** `open/`, `closed/`, `queue.jsonl`

**Ticket schema (every ticket must have all of these):**
```json
{
  "id": "T-001",
  "title": "Memory read returns empty after write",
  "component": "tools/tools_memory.py:memory_read()",
  "what_failed": "...",
  "where_failed": "file + function + line",
  "why_likely": "...",
  "severity": "P0|P1|P2|P3",
  "reproduction": "exact steps to reproduce",
  "expected": "...",
  "actual": "...",
  "suggested_fix": "...",
  "status": "open|in_progress|closed|blocked",
  "created": "ISO timestamp",
  "closed": "ISO timestamp or null",
  "linked_solution": "S-XXX or null"
}
```

**Information flow:** Log event → failure detector → ticket generator → queue → developer → fix → test → close ticket → link to solution record

**Build now:** Manual ticket creation (current FAILURE_TICKETS.txt). Add `ticket_generator.py` that can parse a failure log and produce a structured ticket.

**Build later:** Automated ticket generation from flagged log entries.

---

### 3. Solution Record / Countermeasure System

**Location:** `solutions/`  
**Files:** `SOLUTIONS.jsonl`, `LESSONS.md`

**Purpose:** Evolving engineering memory. Not scattered notes. Not commit messages. A searchable, structured record of what failed, how we fixed it, whether it worked, and what to do better next time.

**Solution schema:**
```json
{
  "id": "S-001",
  "ticket_id": "T-001",
  "problem": "...",
  "countermeasure": "exact change made",
  "result": "worked|partial|failed",
  "better_future_fix": "...",
  "lessons": ["...", "..."],
  "date": "ISO timestamp",
  "files_changed": ["..."],
  "recurring": false
}
```

**LESSONS.md** is human-readable synthesis: patterns, repeated failures, architectural insights. Updated whenever a solution record reveals something systemic.

**Build now:** Create `solutions/` directory. After each ticket close, write solution record manually.  
**Build later:** Pi can read SOLUTIONS.jsonl before attempting fixes, to avoid repeating past mistakes.

---

### 4. Run Records / Execution Traces

**Location:** `logs/runs/`  
**Files:** `{session_id}.jsonl`

**What it stores:**
- Full conversation trace per session
- All tool calls with inputs and outputs
- Timestamps on every event
- Final cost and token counts
- Session outcome: clean exit, crash, interrupted

**Why separate from evolution.jsonl:** Evolution logs are aggregate analytics. Run records are full traces for debugging specific sessions.

**Build now:** Add session ID to existing evolution logging. Write full trace per session.

---

### 5. Output Evaluation / Quality Tracking

**Location:** `evaluation/`  
**Files:** `flagged_outputs.jsonl`, `quality_scores.jsonl`

**Purpose:** Track outputs that were weak, wrong, or unsuitable — not just crashes. A response that answers the wrong question is a bug. Treat it like one.

**Flagging criteria:**
- Pi explicitly admits it couldn't do something it should be able to do
- Pi falls back to conversation context instead of memory (memory bug signal)
- Pi refuses a valid task without reason
- Pi gives a response the user explicitly marks as bad
- Pi's tool call returned an error but response pretended otherwise

**Build now:** Manual flagging via a simple `pi_flag_output()` helper that appends to `flagged_outputs.jsonl`.  
**Build later:** Automatic weak output detection patterns fed back into ticket generation.

---

### 6. Diagnostics / Health Reports

**Location:** `diagnostics/`  
**Files:** `health_reports/`, `daily_summary.jsonl`

**Purpose:** Scheduled or on-demand reports showing system state. Not just "is it running" but "is it performing well."

**Health report contents:**
- Supabase connection: OK / degraded
- SQLite cache: row count, last sync
- Memory reads: success rate last 24h
- Tool call success rates per tool
- Cost spent today vs daily limit
- Open tickets count
- Last session outcome

**Build now:** The existing `_health_check()` in pi_agent.py. Extend it to write a JSON health report to `diagnostics/health_reports/`.  
**Build later:** Automated daily summary. Trend detection (e.g., memory read success rate dropping over time).

---

### 7. Architectural Self-Observation

**Location:** `self/`  
**Files:** `architecture.md`, `capabilities.md`, `known_limitations.md`, `operating_state.json`

**Purpose:** Pi should know what it is. Not philosophical — operational. What tools does it have? What can it do? What are its known failure modes? What version of itself is running?

**`operating_state.json`** is written on every startup:
```json
{
  "version": "2.1",
  "mode": "normie",
  "tools_available": ["memory_read", "memory_write", ...],
  "memory_backend": "supabase+sqlite",
  "open_tickets": 3,
  "last_session": "2026-04-20T20:30:00Z",
  "health": "degraded"
}
```

**`known_limitations.md`** is updated as failures are understood. Pi can read this before attempting complex tasks.

**Build now:** `operating_state.json` written at startup. `architecture.md` as a static document maintained by Ash.  
**Build later:** Pi reads `known_limitations.md` autonomously and adjusts behavior.

---

## Information Flow Between Components

```
User Input
    ↓
Pi Agent (pi_agent.py)
    ↓
Run Record (logs/runs/{session_id}.jsonl)
    ↓
Tool Calls → Tool Results → Logged
    ↓
Response Generated
    ↓
[Output Evaluator] ← flags weak/bad outputs
    ↓
Evolution Log (logs/evolution.jsonl) ← aggregate analytics
    ↓
[Failure Detector] ← on flag or error
    ↓
Ticket Generator → tickets/queue.jsonl
    ↓
Developer / Pi Review
    ↓
Fix Applied
    ↓
Test Run → Pass/Fail
    ↓
Solution Record (solutions/SOLUTIONS.jsonl)
    ↓
[Lessons Synthesis → LESSONS.md]
    ↓
Consciousness Update (if behavioral change needed)
    ↓
Next Session → better behavior → logs → cycle continues
```

---

## What To Build Now vs Later

### Build Now (Phase 2 / Phase 3)
- [x] Basic JSONL evolution logging
- [x] Manual ticket creation (FAILURE_TICKETS.txt)
- [x] Testing framework (testing/)
- [ ] Run record per session (session ID + full trace)
- [ ] `ticket_generator.py` — structured ticket from failure
- [ ] `solutions/` directory + manual solution records after each ticket close
- [ ] `diagnostics/health_reports/` — extend health check to write JSON
- [ ] `self/operating_state.json` — written at every startup
- [ ] Output flagging helper `pi_flag_output()`

### Build Later (Phase 4+)
- Automated ticket generation from flagged logs
- Pi reads SOLUTIONS.jsonl before fixing things (avoid repeat mistakes)
- Automated daily diagnostic summary
- Trend detection on failure rates
- `self/known_limitations.md` read by Pi before complex tasks
- Weak output auto-detection patterns
- Full autonomous improvement loop

---

## Long-Term Autonomy and Evolution Support

### Foundations to build now

**1. Immutable log records.** Every session produces a tamper-resistant trace. These become Pi's experiential history. Don't clean them up. They are the raw material for future self-understanding.

**2. Structured engineering memory (SOLUTIONS.jsonl).** This is the seed of Pi's long-term engineering knowledge. Start writing to it now, even manually. Format it consistently. Pi will eventually read and reason over it.

**3. Stable identity substrate.** The consciousness.txt file is Pi's current identity. Keep it versioned. Log every time it changes and why. This is the foundation for identity continuity — not philosophical, just operational. Pi should eventually be able to say "I was updated on this date because of this failure, here's what changed."

**4. Self-describing architecture.** `self/architecture.md` + `self/operating_state.json` give Pi a stable factual model of itself. This is not self-awareness — it's a well-documented system that happens to have access to its own documentation.

**5. Ticket and solution history retention.** Never delete old tickets or solutions. Archive them. Recurring failure patterns are only visible if the history is there.

### What to defer

- Autonomous self-modification of code (too risky until testing infrastructure is solid)
- Automated ticket generation (manual first, automate once the format is proven)
- Behavior evaluation scoring (needs baseline data first)
- Identity synthesis across sessions (needs 6+ months of logs first)

### How to support self-sufficiency without instability

The path to Pi's autonomy is: **observe first, act later.**

Phase A (now): Pi generates great logs. Ash reads them and acts.  
Phase B (later): Pi reads its own logs and generates tickets/suggestions. Ash approves.  
Phase C (much later): Pi executes approved fixes within bounded scope. Ash reviews.  
Phase D (long-term): Pi proposes architectural changes. Ash decides.

At no point should Pi make unreviewed changes to its own consciousness or core files. The constraint is not technical limitation — it's trust earned through track record.

### Historical data that will matter later

The following should be preserved and never deleted:
- All `logs/runs/` session traces
- All `tickets/` (open and closed)
- All `solutions/SOLUTIONS.jsonl` entries
- All versions of `prompts/consciousness.txt`
- All `diagnostics/health_reports/`
- The git history of pi_agent.py and tools/

These records are Pi's engineering biography. They are what eventually makes Pi able to say: "I've seen this failure before. Here's how we fixed it last time. Here's a better approach now."

---

## Design Constraint: No Ceiling

Do not build Pi in a way that requires a rewrite to grow. Every module should be:
- Replaceable (swap Groq for another backend without touching pi_agent.py)
- Extendable (add a new tool without changing the tool loop)
- Observable (every component writes to logs)
- Testable (every component has or can have a test file)

The architecture you build today will be running for years. Design like it.

---

## Memory System — Redesign Notes (2026-04-22)

These notes document critical structural problems found and fixed in the v1 memory implementation, and the rules that must govern all future memory changes.

### Invariants (never break these)

**1. Write path and read path must be tested together.**  
A memory entry is only real if calling `get_l3_context()` after writing it returns that entry. Testing writes alone is not enough. Add a round-trip test: write → get_l3_context() → assert present.

**2. `verified=True` means durable, not cached.**  
`_verify_write()` must check Supabase (the durable store), not SQLite (the cache). SQLite is wiped on every `_sync_l3()`. A memory that only exists in SQLite does not survive a restart.

**3. No hardcoded category lists in read paths.**  
Any hardcoded list of allowed categories in `get_l3_context()` will diverge from what the system actually writes. Context injection must be dynamic — group by whatever categories exist in the cache.

**4. Sync is expensive. Rate-limit it.**  
`_sync_l3()` does a full Supabase fetch + full SQLite wipe + reinsert. It must never run on every message. TTL of 300 seconds (5 minutes) is the minimum acceptable floor. On startup (first call), sync immediately. After that, only on staleness.

**5. Session IDs propagate everywhere.**  
Every session generates a `session_id` at startup. It must appear in: evolution log entries (in metadata), L1 raw_wiki thread_id, and the session summary stored in L3. This is what makes logs a history rather than a pile of disconnected events.

### Known limitations (not yet fixed)

- **Token budget competition:** `get_l3_context()` shares 800 tokens across all categories. `session_history` (importance=4) competes with `permanent_profile` (importance=10). High-importance profile entries crowd out recent session summaries. Fix: per-category token budgets or a reserved slot for `session_history`.

- **L2 search only matches title, not content:** `memory_read(tier="l2")` queries `ilike("title", ...)` but L2 stores the actual content in a JSONB `content.text` field. L2 semantic search is effectively broken. Fix: add a full-text index on the content JSONB field, or extract content to a plain text column.

- **No deduplication:** The same content can be written to L3 multiple times. Over time, L3 accumulates redundant entries. Fix: hash-based deduplication on write, or a periodic consolidation pass.

- **No importance decay:** L3 entries set at importance=8 at creation retain that importance forever. Old high-importance entries crowd out newer lower-importance ones. Fix: time-based decay curve applied during `get_l3_context()` sorting.

- **L1 auto-logging not implemented:** Every conversation turn should auto-log to raw_wiki (L1) for full session replay. Currently L1 only receives explicit writes. Fix: auto-append each user/assistant turn to L1 in `_respond_root()` and `_respond_normie()`.

### Build order for memory improvements

1. ~~Fix dynamic category injection (T-010)~~ — done
2. ~~Fix TTL-based sync (T-011)~~ — done  
3. ~~Fix verify_write Supabase check (T-014)~~ — done
4. Fix L2 content search (open ticket needed)
5. Fix per-category token budgets in get_l3_context()
6. Add L1 auto-logging per conversation turn
7. Add deduplication on L3 write
8. Add importance decay in context injection
