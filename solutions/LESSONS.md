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
