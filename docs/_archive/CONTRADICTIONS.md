# CONTRADICTIONS — doc-vs-doc and doc-vs-code

**Phase:** 0 — read-only audit
**Date:** 2026-04-25

Every row is a place where two sources of truth disagree. The "What runtime actually does" column cites a file:line or a log entry. The "Resolution" column states which side is wrong. The "Action" column maps to a Phase-1+ deliverable.

---

## C-001 — "Tools are not wired" vs. tools are demonstrably wired

| | |
|---|---|
| **Source A** | [ARCHITECTURE_FIX.md:11-27](ARCHITECTURE_FIX.md), [CRITICAL_FIX_TICKET.md:9-23](CRITICAL_FIX_TICKET.md), [ARCHITECTURE_ADDENDUM.md:11-39](ARCHITECTURE_ADDENDUM.md), [VSCODE_CLAUDE_PROMPT.md:11-49](VSCODE_CLAUDE_PROMPT.md) — all describe Pi as currently broken in P0 because tools are not passed to the API. |
| **Source B** | [pi_agent.py:444 + 475](pi_agent.py#L444) — `self.claude.messages.create(..., tools=self._get_tool_definitions())`. Tool loop in [pi_agent.py:454-482](pi_agent.py#L454-L482) processes `tool_use` blocks, executes via `_execute_tool`, appends `tool_result` blocks, continues. |
| **Contradiction** | Four separate root-level docs say tools aren't wired. They are. |
| **Runtime truth** | Latest `logs/evolution.jsonl` tail (2026-04-25 02:16:52): `{"tools_used": ["memory_read", "memory_read"], ...}` — actual tool calls are landing in production. |
| **Resolution** | Source A is **stale**. Those docs were written against a planned `llm/routing.py` fix path that was abandoned in favour of wiring tools directly in `pi_agent.py`. |
| **Action** | Archive all four docs to `docs/_archive/2026-04-25/` (Phase 1, master prompt §6.1). |

---

## C-002 — README/ABOUT claim "Session persistence ✅" vs. analysis says recall fails

| | |
|---|---|
| **Source A** | [README.md:81](README.md), [ABOUT.md:36](ABOUT.md) — "✅ Session persistence and continuity" / "Session persistence and continuity ✅ Working". |
| **Source B** | [analysis/SUMMARY.md:13-23](analysis/SUMMARY.md) — Pattern P1 documents "silent intent-parse failures cascading into hallucinated capabilities" (T-019 still open). [analysis/tickets.jsonl T-019](analysis/tickets.jsonl) describes Pi mimicking memory storage in normie mode and the user later finding nothing was saved. |
| **Contradiction** | Public docs claim session persistence works; behavioural evidence says recall fails in real conversations. |
| **Runtime truth** | Writes succeed (verified by [tools_memory.py:401-422](tools/tools_memory.py#L401-L422) and S-008's `_verify_write` fix). The failure mode is **read-side**: Claude's natural-language memory queries don't always match stored content (master prompt §1.8). No automated test exercises the round-trip via the tool loop ([master prompt §1.9](PI_MASTER_PROMPT.md), and verified by reading every test file in [testing/](testing/)). |
| **Resolution** | Source A is **overclaim**. Storage works; retrieval through the LLM's own queries does not always work. |
| **Action** | Phase 1: rewrite the capability tables in README.md and ABOUT.md to mark this row as "🟡 Working (needs round-trip test)". Phase 3: implement [testing/test_memory_roundtrip.py](testing/test_memory_roundtrip.py) per master prompt §6 Phase 3. |

---

## C-003 — README claims `memory_read(query, tier)` searches all tiers; code excludes L1

| | |
|---|---|
| **Source A** | [README.md:20](README.md) — "memory_read, memory_write, memory_delete — structured memory operations" (implies generic search across tiers). [tools_memory.py:50-61](tools/tools_memory.py#L50-L61) docstring explicitly says "tier: l1/l2/l3 or None for all". |
| **Source B** | [tools_memory.py:96](tools/tools_memory.py#L96) — `if tier == "l1":` — only fires when the caller explicitly passes `tier="l1"`. With `tier=None` the function only searches L3 ([tools_memory.py:64](tools/tools_memory.py#L64)) and L2 ([tools_memory.py:83](tools/tools_memory.py#L83)). |
| **Contradiction** | Docstring contract says "None for all"; code excludes L1 from the implicit search. |
| **Runtime truth** | Same as Source B. Confirmed by reading the conditional gates in `memory_read`. |
| **Resolution** | This is open ticket [T-017](analysis/tickets.jsonl). Conservative fix: correct the docstring to "tier=None searches L3+L2 only; use tier='l1' explicitly for L1 archive." Aggressive fix: include L1 with a low default limit. Master prompt §6 Phase 3 picks conservative for now. |
| **Action** | Phase 3, fix T-017. |

---

## C-004 — `evolution.py` writes `tools_used`, reads `tool_calls`

| | |
|---|---|
| **Source A** | [evolution.py:48](evolution.py#L48) — `"tools_used": [tc.get("name", "") for tc in tool_calls],` (write path). |
| **Source B** | [evolution.py:90-95](evolution.py#L90-L95) — `for tool_call in interaction.get("tool_calls", []): tool_name = tool_call.get("name", "unknown") ...` (read path). |
| **Contradiction** | The analyzer reads a field name (`tool_calls`) that the logger never writes. |
| **Runtime truth** | Confirmed by inspecting the tail of `logs/evolution.jsonl`: every entry has `tools_used: [...]`, no entry has a top-level `tool_calls` field. Therefore `analyze_performance()` always returns empty `tool_usage` and `tool_success_rates` dicts, and `identify_improvements()` (which reads them) operates on empty data, and the monthly self-review's "tool failure" branch is never triggered. |
| **Resolution** | Code-level bug — schema drift. |
| **Action** | Phase 2, master prompt §6 Phase 2. Detailed in `SCHEMA_MISMATCHES.md` SM-001. |

---

## C-005 — `app/state.py` schema vs. `tools_memory.py` schema

| | |
|---|---|
| **Source A** | [app/state.py:17-145](app/state.py#L17-L145) creates 10 tables: `users`, `devices`, `threads`, `messages`, `memories`, `documents`, `tool_runs`, `cost_log`, `settings`, `audit_logs`. |
| **Source B** | [tools/tools_memory.py:30-48](tools/tools_memory.py#L30-L48) creates exactly one table: `l3_cache`. The runtime never imports `app.state` (verified: `grep -E '^(from |import ).*app\.state'` returns no results). |
| **Contradiction** | Two divergent schemas in the same `data/pi.db` SQLite file, one of them never instantiated. |
| **Runtime truth** | Same as Source B. [data/README.md:35-37](data/README.md#L35) already acknowledges this honestly: "Legacy Tables (Not Used by Agent) — These were from the old system. Safe to ignore." |
| **Resolution** | `app/state.py` is dead code. The acknowledgement in `data/README.md` is correct. |
| **Action** | Phase 4 (per master prompt §6.4): archive `app/state.py` to `_archive/code/2026-04-25/app_state.py`. Detailed in `DEAD_CODE.md` DC-002. |

---

## C-006 — `llm/routing.py` model string `claude-haiku-4-6` vs. runtime model `claude-sonnet-4-6`

| | |
|---|---|
| **Source A** | [llm/routing.py:139](llm/routing.py#L139) — `model="claude-haiku-4-6"`. The same string also appears in the (planned-but-not-applied) edits in [ARCHITECTURE_FIX.md:226](ARCHITECTURE_FIX.md#L226), [CRITICAL_FIX_TICKET.md:30](CRITICAL_FIX_TICKET.md#L30), [VSCODE_CLAUDE_PROMPT.md:537](VSCODE_CLAUDE_PROMPT.md#L537) (with `-20251001` suffix). |
| **Source B** | [pi_agent.py:440](pi_agent.py#L440), [pi_agent.py:471](pi_agent.py#L471), [core/research_mode.py:34](core/research_mode.py#L34) — `model="claude-sonnet-4-6"`. |
| **Contradiction** | Different Claude model strings in dead vs. live code paths. |
| **Runtime truth** | The runtime uses `claude-sonnet-4-6` (verified by Source B and by the `model` field in latest `logs/evolution.jsonl` entries). The Haiku string is only present in unwired code. |
| **Resolution** | The Haiku string is an artifact of the abandoned routing-layer plan. If/when `llm/routing.py` is archived (Phase 4), the contradiction disappears. |
| **Action** | Phase 4: archive `llm/routing.py`. Until then, no code uses the Haiku string, so this is documentation-only confusion. |

---

## C-007 — Stale fix docs reference modules that don't exist

| | |
|---|---|
| **Source A** | [ARCHITECTURE_FIX.md:111-115, 197-203](ARCHITECTURE_FIX.md), [CRITICAL_FIX_TICKET.md:107-132](CRITICAL_FIX_TICKET.md), [VSCODE_CLAUDE_PROMPT.md:412-447, 500-505](VSCODE_CLAUDE_PROMPT.md) — reference `memory/sqlite_store.py`, `memory/supabase_store.py`, `memory/l3_builder.py`, `app/main.py`, `llm/tools.py`, `llm/tool_executor.py`. |
| **Source B** | The repo. None of these files exist. Verified via `ls e:/pi/` and the Glob tool: no `memory/` directory at all; no `app/main.py`; no `llm/tools.py` or `llm/tool_executor.py`. |
| **Contradiction** | Multiple "implementation specs" describe a module layout that was never built. |
| **Runtime truth** | The architecture pivoted: tools are wired in `pi_agent.py`, memory is in `tools/tools_memory.py`, no separate `memory/` package exists. |
| **Resolution** | Source A docs are **stale**. They describe a hypothetical architecture, not the built one. |
| **Action** | Phase 1: archive all four stale fix docs (overlaps with C-001's action). |

---

## C-008 — `prompts/system.txt` documents capabilities, doesn't list tools

| | |
|---|---|
| **Source A** | [prompts/system.txt:11](prompts/system.txt#L11) — "Pi DOES save every conversation to a local database and cloud storage (Supabase). If asked, confirm this honestly." |
| **Source B** | The runtime never auto-saves every conversation to the durable Supabase store. L1 raw_wiki is only written to via explicit `memory_write(tier="l1")` ([tools/tools_memory.py:220-238](tools/tools_memory.py#L220-L238)). The `_respond_root` and `_respond_normie` paths log to `logs/evolution.jsonl` (a flat file, not Supabase) — see [pi_agent.py:494-505](pi_agent.py#L494-L505) and [pi_agent.py:574-585](pi_agent.py#L574-L585). |
| **Contradiction** | The base system prompt overstates persistence. The L1 auto-logging called out as missing in [ARCHITECTURE_DIRECTION.md:373-375](ARCHITECTURE_DIRECTION.md#L373-L375) ("L1 auto-logging not implemented") is exactly what `system.txt` claims is happening. |
| **Runtime truth** | Conversations are written to a local JSONL log per interaction. They are not auto-archived to Supabase per turn. Session summaries are written on exit ([pi_agent.py:766-777](pi_agent.py#L766-L777)) but individual turns are not. |
| **Resolution** | Prompt overclaim. |
| **Action** | Phase 5 (master prompt §5): rewrite `prompts/system.txt` and reconcile this line with the actual auto-logging state (currently absent). Or implement the L1 auto-logging in Phase 3+. |

---

## C-009 — `consciousness.txt` lists `web_search` tool that does not exist

| | |
|---|---|
| **Source A** | [prompts/consciousness.txt:82-84, 166](prompts/consciousness.txt#L82-L84), [prompts/consciousness.txt:165-167](prompts/consciousness.txt#L165-L167) — references `web_search` tool, "Search is free (web_search), use it". |
| **Source B** | [pi_agent.py:140-238](pi_agent.py#L140-L238) — `_get_tool_definitions()` returns 8 tools: `memory_read`, `memory_write`, `memory_delete`, `execute_python`, `execute_bash`, `read_file`, `modify_file`, `create_file`. **No `web_search`.** |
| **Contradiction** | The identity prompt tells Pi to use a tool that isn't defined. |
| **Runtime truth** | If Pi attempts a `web_search` tool call, the dispatch in [pi_agent.py:319-321](pi_agent.py#L319-L321) returns `{"error": "Unknown tool: web_search"}`. (In practice the LLM has likely never tried, since the schema isn't passed.) |
| **Resolution** | Prompt drift. Either implement the tool or remove the references. |
| **Action** | Phase 5 (prompt-engineering pass): scrub `web_search` references from `consciousness.txt`. Add a ticket for the future tool itself if Ash wants it. |

---

## C-010 — `consciousness.txt` says "Your model: Claude (Sonnet 4)" — minor version drift

| | |
|---|---|
| **Source A** | [prompts/consciousness.txt:259](prompts/consciousness.txt#L259) — "Your model: Claude (Sonnet 4)". Knowledge cutoff stated as January 2025. |
| **Source B** | [pi_agent.py:440](pi_agent.py#L440) — `model="claude-sonnet-4-6"`. |
| **Contradiction** | Self-described as Sonnet 4; actually Sonnet 4.6. |
| **Runtime truth** | Sonnet 4.6 (per the model string in the API call, confirmed in `logs/evolution.jsonl` entries). |
| **Resolution** | Minor prompt staleness. |
| **Action** | Phase 5 update. Low priority (the model itself doesn't behave differently because of self-description text). |

---

## C-011 — `FAILURE_TICKETS.txt` open status vs. closed-ticket evidence

| | |
|---|---|
| **Source A** | [FAILURE_TICKETS.txt:18-25](FAILURE_TICKETS.txt#L18-L25) — Ticket #001 (memory read failure): "Status: 🔴 OPEN - VERIFIED IN TEST". Tickets #002-#005 also marked open. |
| **Source B** | (a) `tickets/closed/T-006` through `T-016` cover much of the territory of #001-#005. (b) [solutions/SOLUTIONS.jsonl S-006](solutions/SOLUTIONS.jsonl) explicitly resolves the session-summary-on-exit bug from #002. (c) Single-fact recall is exercised by [testing/test_memory.py:117-141](testing/test_memory.py#L117-L141) and works against the current `MemoryTools` implementation. |
| **Contradiction** | #001-#005 are documented as P0/open in `FAILURE_TICKETS.txt`, but several have been demonstrably resolved in the new ticket system (T-006+). |
| **Runtime truth** | Status by ticket: #001 (memory reads broken) — partial. Unit-test reads work; tool-loop round-trip is unverified, which is exactly what the analysis-pipeline tickets describe (recall via Claude's queries fails). #002 (session persistence) — resolved. #003 (normie isolation) — resolved by S-011 (cross-mode `self.messages`). #004 (file tracking) — resolved (file ops auto-write to memory at [pi_agent.py:299-317](pi_agent.py#L299-L317)). #005 (research auto-save) — resolved at [pi_agent.py:389-394](pi_agent.py#L389-L394). |
| **Resolution** | `FAILURE_TICKETS.txt` is **stale** as a status document. It remains valuable as a historical record of how Pi looked on 2026-04-20. |
| **Action** | Phase 1: archive `FAILURE_TICKETS.txt` to `docs/_archive/2026-04-25/`. The salvageable parts (anything still live) are already represented by tickets in `analysis/tickets.jsonl` or, post-promotion, in `tickets/open/`. |

---

## C-012 — Multiple docs reference `claude-haiku-4-5-20251001`

| | |
|---|---|
| **Source A** | [ARCHITECTURE_FIX.md:226, 260](ARCHITECTURE_FIX.md), [CRITICAL_FIX_TICKET.md:30](CRITICAL_FIX_TICKET.md), [VSCODE_CLAUDE_PROMPT.md:36, 537, 587](VSCODE_CLAUDE_PROMPT.md) — describe the system using `claude-haiku-4-5-20251001`. |
| **Source B** | No file in the repo currently contains that string in a live code path. The runtime uses `claude-sonnet-4-6`. |
| **Contradiction** | Several docs describe a model that the runtime never actually used. |
| **Runtime truth** | Same as Source B. The string was speculative — part of the abandoned `llm/routing.py` fix plan. |
| **Resolution** | Doc-only artefact. Resolved when the stale fix docs are archived. |
| **Action** | Same as C-001 / C-007 — archive in Phase 1. |

---

## Summary of contradictions by phase

| Phase | Contradictions to resolve |
|---|---|
| Phase 1 (docs collapse) | C-001, C-002, C-007, C-008 (partial), C-011, C-012 |
| Phase 2 (evolution telemetry fix) | C-004 |
| Phase 3 (memory round-trip) | C-002 (round-trip test), C-003 (T-017 fix) |
| Phase 4 (refactor + dead code archive) | C-005, C-006 |
| Phase 5 (prompt engineering) | C-008 (rewrite system.txt), C-009 (web_search), C-010 (model name) |
