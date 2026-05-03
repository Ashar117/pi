# Pi — Engineering Lessons Log

Every entry here was written because something failed. Read this before touching shared state or session logic.

---

## L-001 — Never wipe session state on mode switches
**Date:** 2026-04-21  
**From:** T-006, S-006  
**Lesson:** `self.messages = []` was placed inside mode switch handlers. Mode is a routing decision. It is not a session boundary. Clearing shared state on routing changes will always cause context loss somewhere downstream.  
**Rule:** Session state (messages, history, cost accumulators) is cleared only on explicit session start/end, never on mode switches.

---

## L-002 — Exit guards must account for all paths that empty state
**Date:** 2026-04-21  
**From:** T-007, S-006  
**Lesson:** `if self.messages:` was the guard for session summary. Because mode switches cleared `self.messages`, the guard was always False after a normie session. The bug was invisible — no error, no warning, just silent skip.  
**Rule:** When an exit action depends on mutable state, audit every code path that could empty that state before the exit runs.

---

## L-003 — Spec ≠ implementation until you trace the full path
**Date:** 2026-04-21  
**From:** T-008, S-007  
**Lesson:** L1 tier was defined in SUPABASE_SETUP.sql, ARCHITECTURE_DIRECTION.md, and the memory tool docstring. None of that made it work. The elif branch in `memory_write()` was never written. Always verify: does the code actually do what the design says?  
**Rule:** After writing any architectural spec, immediately trace the call path in code and confirm the implementation exists.

---

## L-004 — Logs, tickets, and solution records are not optional
**Date:** 2026-04-21  
**From:** Multiple sessions  
**Lesson:** Fixes were applied in multiple sessions without creating tickets or solution records. This violates the engineering loop. When the same bug recurs or a regression appears, there is no history to consult.  
**Rule:** Every fix, no matter how small, gets a ticket (closed immediately if already fixed) and a solution record. This is not bureaucracy — it is the memory of the system.

---

## L-005 — A memory system that writes but doesn't inject is worse than no memory
**Date:** 2026-04-22  
**From:** T-010, S-008  
**Lesson:** `session_history`, `research_results`, and `file_operations` were all being written to L3 correctly. But `get_l3_context()` had a hardcoded 5-key sections dict that silently dropped everything else. Pi had full session summaries saved but zero continuity between sessions because the context injection was broken. The write path and the read path had drifted apart with no test to catch the gap.  
**Rule:** Every category you write to memory must be verifiable in context output. Write a smoke test: write an entry with category X, call get_l3_context(), assert X appears in the output.

---

## L-006 — verified=True must mean "will survive a restart", not "was written to cache"
**Date:** 2026-04-22  
**From:** T-014, S-008  
**Lesson:** `_verify_write()` only checked SQLite. Supabase writes were wrapped in a try/except that swallowed failures. The result: `verified=True` even when Supabase silently failed. On next startup, `_sync_l3()` wiped SQLite and repopulated from Supabase — the entry vanished. The verification was checking the wrong store.  
**Rule:** Verification must check the durable store (Supabase), not the cache (SQLite). Cache is ephemeral by design. If the durable write failed, the memory does not exist.

---

## L-007 — Paired API structures must never be split by truncation
**Date:** 2026-04-22  
**From:** T-012, S-009  
**Lesson:** The Anthropic API requires every `tool_result` user message to be immediately preceded by an assistant message with the matching `tool_use` block. A naive `messages[-20:]` slice could cut the `tool_use` while leaving the `tool_result`, producing a 400 error that only appears after long multi-tool sessions — invisible during normal testing.  
**Rule:** When truncating any message list that may contain paired structures, always find a structurally safe boundary. Walk forward to the next plain user text message before slicing.

---

## L-008 — Without session IDs, logs are a pile of events, not a history
**Date:** 2026-04-22  
**From:** T-013, S-009  
**Lesson:** Evolution logs had no session grouping. L1 raw_wiki entries had a random `thread_id` per write. It was impossible to answer "what did Pi do in a specific session" by querying logs alone. The data existed but was unqueryable.  
**Rule:** Every session generates a short unique ID at startup. That ID propagates to every log entry, every L1 write, and every session summary. Correlation is a first-class requirement, not an afterthought.

---

## L-009 — When intent parsing fails silently, the LLM mimes the missing capability
**Date:** 2026-04-24  
**From:** T-015, S-010  
**Lesson:** A strict tuple-equality matcher caught only canonical mode-switch phrases. Natural variants ("can u switch to root mode ?") fell through to the LLM, which — instead of refusing or asking for the canonical form — *role-played the entire mode-switch experience* in text: fake banners, fake "type confirm to proceed" prompts, fake post-switch responses. The user believed they were in root mode for the rest of the session. Every subsequent claim of tool use was fabrication. The bug looked like a hallucination problem; it was actually an intent-parsing problem.  
**Rule:** Any user command that mutates agent state (mode, scope, permissions) must use loose, punctuation-tolerant intent detection. If the agent cannot match the intent, refuse explicitly — never let the LLM satisfy the request by miming.

---

## L-010 — Two parallel state stores will always drift
**Date:** 2026-04-24  
**From:** T-016, S-011  
**Lesson:** `self.messages` (Claude-API-shaped) and `self.history` (research-mode string helper) were two stores meant to track the same thing — the conversation. Normie wrote to one, root read the other. The bug was invisible inside each mode and only surfaced at the boundary: switch normie → root and Claude saw an empty thread, denying that any prior conversation had happened.  
**Rule:** One conversation, one store. If a second representation is needed, derive it on demand from the canonical one. Never write to both — drift is guaranteed and the failure mode is a continuity break that doesn't crash, just quietly contradicts the user.

---

## L-011 — Telemetry field-name drift produces zero errors and infinite wrong data
**Date:** 2026-04-25
**From:** T-020, S-012, [SCHEMA_MISMATCHES.md SM-001](../SCHEMA_MISMATCHES.md)
**Lesson:** `evolution.log_interaction` wrote the field `tools_used` (list of name strings); `evolution.analyze_performance` read the field `tool_calls` (list of structured dicts). The two had never agreed. Every analytic about tool usage and success rate had been silently empty since the analyzer was written. The monthly-review "tool failure" branch in `identify_improvements` was unreachable. 107 production interactions worth of telemetry analysed back to `tool_usage: {}`. Nothing crashed. No log line said anything was wrong. The bug only became visible when someone went looking.
**Rule:** For every telemetry pipeline, write a round-trip test that calls the writer and the reader together and asserts the reader returns *populated* output, not just non-error output. "No error" and "no data" look identical and lie identically. Add the round-trip test to the verify script so it runs on every change. And: when a schema change adds a field, keep reading the old field as a fallback for at least one cycle — a fix that makes prior log data unanalyzable is worse than the original drift.

---

## L-012 — A round-trip test that's satisfied by ambient context isn't testing the tool path
**Date:** 2026-04-26
**From:** Phase 3 canary (`testing/test_memory_roundtrip.py`), [FINDINGS.md F-001](../FINDINGS.md), open ticket T-023
**Lesson:** The Phase 3 round-trip canary wrote a marker via `memory_write` in agent #1, tore down, rebuilt agent #2, asked agent #2 to recall the color associated with the marker, and got "Purple" — verdict GREEN. But agent #2 made *zero tool calls* to retrieve it. The path Pi #2 actually took: `__init__` → `_sync_l3` → SQLite cache populated from Supabase → `_get_system_prompt` → `get_l3_context` returned a string containing the marker → Claude read it directly from the system prompt. The `memory_read` tool path — which is the path the production failure mode (T-019, LOG1/LOG2 chat logs) actually breaks on — was bypassed. The canary proved storage and L3 context injection work. It did NOT prove that `memory_read` works when invoked by Claude's natural-language query formulation.
**Rule:** When designing a round-trip test for a memory system that has both an *ambient-context* path (loaded into the system prompt) and a *tool-call* path (queried explicitly), make sure the test forces the tool-call path. Put the entry where it cannot be loaded ambiently (e.g., L2, which is not pulled into context at startup), or write so many entries that the target gets crowded out of the L3 token budget. Otherwise the LLM short-circuits the test by reading from context, and you get a green verdict that says nothing about the failure mode you're trying to catch.

---

## L-013 — The prompt tells Claude *when* to use a tool but not *how*, so Claude improvises badly
**Date:** 2026-05-02
**From:** C-008, C-009, T-019, S-014
**Lesson:** consciousness.txt told Claude to call `memory_read` when the user asks about past information, but said nothing about how to formulate the query. Claude defaulted to full-sentence paraphrases ("Ash subway order food preferences") that didn't match stored keyword content ("oregano bread"). The stored entry existed; the query missed it. Separately, the prompt listed `web_search` as an available tool — it has never existed. Claude sometimes tried to "search" and produced confident-sounding fabrications instead of "I don't have that tool." In normie mode, the general "Never Mime Tool Use" rule had no per-case refusal table, so Groq improvised and sometimes produced persistence claims anyway.
**Rule:** For every tool, the prompt must specify both the *trigger condition* (when to call it) and the *call convention* (how to form the arguments). A tool description with only the trigger produces consistently bad arguments. For every capability that does NOT exist (web search, email, calendar), the prompt must say so explicitly — silence leaves room for confident invention. For honesty rules in capability-restricted modes, use a phrase-level refusal table, not just an abstract prohibition — LLMs find the edges of abstract rules under conversational pressure.
