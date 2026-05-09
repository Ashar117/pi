# RECONCILIATION — docs vs code

**Phase:** 0 — read-only audit
**Date:** 2026-04-25
**Method:** Every row was checked against the actual file/line in the repo at the time of writing. No claim here is from memory of the docs alone.

Status legend:
- **CANONICAL** — current, accurate, source of truth.
- **CANONICAL (overclaim)** — current and intended, but contains a claim the code does not back.
- **SUPERSEDED** — replaced by a later doc; keep for history but not authoritative.
- **CONTRADICTED** — contains claims the runtime contradicts; needs correction or archival.
- **STALE** — describes an architecture that was pivoted away from; should be archived.
- **AUDIT_TRAIL_KEEP** — append-only history (chat logs, tickets, solutions, lessons). Don't touch.

---

## Root docs

| File | mtime | Claimed role | Claims (re: working/broken) | What the code actually shows | Status |
|---|---|---|---|---|---|
| [README.md](README.md) | 2026-04-24 | Repo entry point + tech reference | `✅ Session persistence and continuity`, `✅ Three-tier memory`, `✅ Evolution tracking`, `🚧 Autonomous ticket gen`, `🚧 Self-improvement loop` | Tool loop is wired ([pi_agent.py:454-482](pi_agent.py#L454-L482)). Session_id propagates ([pi_agent.py:68](pi_agent.py#L68); confirmed in [logs/evolution.jsonl](logs/evolution.jsonl)). But: evolution analytics are silently empty ([evolution.py:48,90](evolution.py#L48) — schema drift); memory round-trip via tool loop is not exercised by tests; `analyze performance` walks empty `tool_usage` data | CANONICAL (overclaim) |
| [ABOUT.md](ABOUT.md) | 2026-04-24 | Public-facing vision | Capability table all `✅ Working` for: 3-mode routing, 3-tier memory, tool use, session persistence, engineering loop, conversation analysis, cost gating, health diagnostics | Same overclaim as README. The `✅ Session persistence and continuity` row in particular is not verified by an end-to-end tool-loop round-trip test — [analysis/SUMMARY.md](analysis/SUMMARY.md) and [analysis/tickets.jsonl](analysis/tickets.jsonl) document recall failures from production sessions | CANONICAL (overclaim) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 2026-04-19 | Architecture reference | File responsibility table marks `routing.py: No (legacy)` and `state.py: No (legacy schema)` | Both confirmed dead by import graph: nothing imports `llm.routing` or `app.state` (grep across all `.py` returns zero importers) | CANONICAL |
| [ARCHITECTURE_DIRECTION.md](ARCHITECTURE_DIRECTION.md) | 2026-04-24 | Canonical design doc | Memory-system invariants in §"Memory System — Redesign Notes". Marks T-010, T-011, T-014 as done; flags L2 content search, per-category token budgets, L1 auto-logging as open | Matches code: [tools_memory.py:293-370](tools/tools_memory.py#L293-L370) implements dynamic categories (T-010); `_last_sync` TTL 300s ([tools_memory.py:26-27,302-305](tools/tools_memory.py#L26-L27)); `_verify_write` checks both stores ([tools_memory.py:401-422](tools/tools_memory.py#L401-L422)) | CANONICAL |
| [USER_GUIDE.md](USER_GUIDE.md) | 2026-04-19 | User-facing command list | Lists `root mode`, `normie mode`, `research mode`, `analyze performance`, `exit` | Matches [pi_agent.py:341-399](pi_agent.py#L341-L399). Doesn't mention loose mode-switch matching from S-010 (e.g. "switch to root mode") but the runtime supports it now | CANONICAL (mildly stale on S-010 detail) |
| [ARCHITECTURE_FIX.md](ARCHITECTURE_FIX.md) | 2026-04-24 | "Tool integration fix spec" | Says tools are not wired; prescribes creating `llm/tools.py`, `llm/tool_executor.py`, `memory/sqlite_store.py`; modifying `llm/routing.py` and `app/main.py` | Files referenced do not exist. The actual fix took a different path: tools are wired in [pi_agent.py:140-238 (definitions)](pi_agent.py#L140-L238) and [pi_agent.py:454-482 (loop)](pi_agent.py#L454-L482), not in `llm/`. `app/main.py` does not exist | STALE |
| [CRITICAL_FIX_TICKET.md](CRITICAL_FIX_TICKET.md) | 2026-04-24 | "P0 critical: tool hallucination" | "Tools not wired to LLM API calls" | Was true at the time the ticket was written against the `llm/routing.py` path. That path was abandoned; tools are now wired in `pi_agent.py`. Latest log entry [logs/evolution.jsonl](logs/evolution.jsonl) tail shows actual `tools_used: ["memory_read","memory_read"]` calls landing | STALE |
| [ARCHITECTURE_ADDENDUM.md](ARCHITECTURE_ADDENDUM.md) | 2026-04-24 | "Tool hallucination crisis" | Same "Pi has no tools" claim; lists fix items targeting `llm/`, `memory/`, `app/main.py` | Same as above — the proposed fix path was not the path taken. Still describes the system as broken in P0 state | STALE |
| [VSCODE_CLAUDE_PROMPT.md](VSCODE_CLAUDE_PROMPT.md) | 2026-04-24 | "Implementation instructions for the fix" | Tells the assistant to create `llm/tools.py`, `llm/tool_executor.py`, modify `memory/sqlite_store.py`, `llm/routing.py`, `app/main.py`, and add a `memory` table to the SQL schema | None of those files/tables exist. The fix landed via `pi_agent.py` instead; running this prompt against the current repo would create a parallel, incompatible second tool path | STALE |
| [VSCODE_MASTER_PROMPT.txt](VSCODE_MASTER_PROMPT.txt) | 2026-04-20 | Operating protocol for VSCode Claude | Describes a general-purpose anti-hallucination workflow. Predates the conversation analysis pipeline. Replaced functionally by [PI_MASTER_PROMPT.md](PI_MASTER_PROMPT.md) | Superseded by `PI_MASTER_PROMPT.md` (the prompt this audit was launched from) | SUPERSEDED |
| [DEPLOYMENT_PROTOCOL.txt](DEPLOYMENT_PROTOCOL.txt) | 2026-04-20 | "Step-by-step execution guide" for the 5-ticket fix run | References tickets #001-#005 from `FAILURE_TICKETS.txt`; predates the ticket schema redesign (T-006+) | Tickets #001-#005 are partially resolved (see `FAILURE_TICKETS.txt` row below). The protocol's references to `VSCODE_CLAUDE_FIX_COMMANDS.txt` and a 4-6 hour fix sprint are no longer the operating model | SUPERSEDED |
| [EXECUTIVE_SUMMARY.txt](EXECUTIVE_SUMMARY.txt) | 2026-04-20 | "Pi ready for systematic execution" overview | Summarises the 4-doc fix package (VSCODE_MASTER_PROMPT.txt + TESTING_FRAMEWORK.txt + DEPLOYMENT_PROTOCOL.txt + FAILURE_TICKETS.txt) | The whole package is now superseded by the canonical engineering loop in `ARCHITECTURE_DIRECTION.md` and `PI_MASTER_PROMPT.md` | SUPERSEDED |
| [FAILURE_TICKETS.txt](FAILURE_TICKETS.txt) | 2026-04-20 | Original 5-ticket bug list | #001 memory reads broken (P0); #002 session persistence broken (P0); #003 normie isolation; #004 file tracking; #005 research auto-save | Status of each per current code: #001 — bulk and single reads work in unit tests ([testing/test_memory.py:41-141](testing/test_memory.py#L41-L141)) but tool-loop round-trip is unverified; #002 — resolved by S-006 (session summary now writes; verified in [pi_agent.py:766-777](pi_agent.py#L766-L777)); #003 — partially resolved by S-011 (cross-mode continuity, [pi_agent.py:538-585](pi_agent.py#L538-L585)); #004 — file ops auto-log ([pi_agent.py:299-317](pi_agent.py#L299-L317)); #005 — research auto-save implemented ([pi_agent.py:389-394](pi_agent.py#L389-L394)) | CONTRADICTED (status field outdated; merge resolved items into closed tickets, retire) |
| [TESTING_FRAMEWORK.txt](TESTING_FRAMEWORK.txt) | 2026-04-20 | Testing framework spec | Prescribes the test files in [testing/](testing/) | Real test files exist and match this layout broadly. Doc itself is now superseded by the actual code in `testing/` | SUPERSEDED |
| [LICENSE](LICENSE) | 2026-04-24 | MIT license | — | — | CANONICAL |
| [requirements.txt](requirements.txt) | 2026-04-19 | Python deps | Lists anthropic, groq, google-generativeai, supabase, python-dotenv, ollama | All present in code imports. `ollama` is only used by dead code [llm/routing.py:5](llm/routing.py#L5); could be removed when that file is archived | CANONICAL |
| [.gitignore](.gitignore) | 2026-04-24 | Privacy/secret rules | gitignores `app/config.py`, `.env`, `data/`, `logs/`, `analysis/chat_logs.txt`, `pi_env/`, `*.backup.*`, `temp_exec.py` | Matches reality | CANONICAL |
| [test_progress.txt](test_progress.txt) | 2026-04-20 | A test-run scratch note | Lists 5 things Ash told Pi to remember on 2026-04-20 (the original stress-test data) | Personal notepad fragment from old stress test. Not referenced by anything | STALE (candidate to archive or delete; predates analysis/ pipeline) |
| [pi_dna.txt](pi_dna.txt) | 2026-04-19 | "Project DNA" — full design history (167KB) | Not read in full this session (size). Referenced in `DEPLOYMENT_PROTOCOL.txt` and `EXECUTIVE_SUMMARY.txt` as `PI_PROJECT_DNA.txt` | Predates `ARCHITECTURE_DIRECTION.md`. Master prompt §6.3 says "salvage `MODULE_TEMPLATE.py` from §18" — implies useful content remains | SUPERSEDED (likely; flagged for closer review in Phase 1) |
| [SUPABASE_SETUP.sql](SUPABASE_SETUP.sql) | 2026-04-20 | Supabase schema | Creates `l3_active_memory`, `organized_memory`, `raw_wiki` + RLS policies + Ash profile seed | Matches every Supabase table touched by [tools_memory.py](tools/tools_memory.py) | CANONICAL |
| [PI_MASTER_PROMPT.md](PI_MASTER_PROMPT.md) | 2026-04-24 | Operating protocol for this VS Code Claude session | The prompt the current audit was launched from | This document is the authority for §0-§10 of the work being done now | CANONICAL |

---

## `prompts/`

| File | mtime | Claimed role | Claims | Reality | Status |
|---|---|---|---|---|---|
| [prompts/consciousness.txt](prompts/consciousness.txt) | 2026-04-24 | Pi's identity prompt | "Never Mime Tool Use" section (lines 51-56) lists banned phrases and banners | Loaded by [pi_agent.py:46-47](pi_agent.py#L46-L47). Section is present and matches the post-S-010 prompt-engineering pass | CANONICAL |
| [prompts/system.txt](prompts/system.txt) | 2026-04-08 | Base system prompt for non-Claude agents | 19 lines, says nothing about tools | Loaded by [core/research_mode.py:23](core/research_mode.py#L23) and used as the base for Claude/Gemini/Groq personas in research mode. Matches that role. Master prompt §5.2 (Phase 5) flags it for expansion to include tool list — for now, accurate to its purpose | CANONICAL |

---

## `analysis/`

| File | mtime | Role | Status |
|---|---|---|---|
| [analysis/README.md](analysis/README.md) | 2026-04-24 | How the conversation analysis pipeline works | CANONICAL |
| [analysis/WORKFLOW.md](analysis/WORKFLOW.md) | 2026-04-24 | Rubric for chat-log analysis | CANONICAL |
| [analysis/SUMMARY.md](analysis/SUMMARY.md) | 2026-04-24 | Pattern catalog (P1-P3) | CANONICAL |
| [analysis/chat_logs.txt](analysis/chat_logs.txt) | 2026-04-24 | Raw chat logs (gitignored) | AUDIT_TRAIL_KEEP |
| [analysis/tickets.jsonl](analysis/tickets.jsonl) | 2026-04-24 | T-015 through T-019 | AUDIT_TRAIL_KEEP |

---

## `solutions/`

| File | mtime | Role | Status |
|---|---|---|---|
| [solutions/SOLUTIONS.jsonl](solutions/SOLUTIONS.jsonl) | 2026-04-24 | S-006 through S-011, append-only solution records | AUDIT_TRAIL_KEEP |
| [solutions/LESSONS.md](solutions/LESSONS.md) | 2026-04-24 | L-001 through L-010, append-only lessons | AUDIT_TRAIL_KEEP |

---

## `tickets/`

| Ticket file | Status field | Verified against runtime | Notes |
|---|---|---|---|
| [tickets/closed/T-006-messages-cleared-on-mode-switch.json](tickets/closed/T-006-messages-cleared-on-mode-switch.json) | closed | Yes — [pi_agent.py:344](pi_agent.py#L344) comment "never clear self.messages, session context must survive mode changes" | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-007-session-summary-never-writes.json](tickets/closed/T-007-session-summary-never-writes.json) | closed | Yes — [pi_agent.py:766-777](pi_agent.py#L766-L777) writes summary on exit | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-008-l1-tier-unknown.json](tickets/closed/T-008-l1-tier-unknown.json) | closed | Yes — [tools_memory.py:220-238](tools/tools_memory.py#L220-L238) implements L1 write | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-009-mode-switch-command-too-strict.json](tickets/closed/T-009-mode-switch-command-too-strict.json) | closed | Yes — first-pass fix; superseded by S-010 (T-015) for stronger natural-language matching | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-010-context-injection-categories-broken.json](tickets/closed/T-010-context-injection-categories-broken.json) | closed | Yes — [tools_memory.py:329-370](tools/tools_memory.py#L329-L370) does dynamic grouping | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-011-sync-l3-called-every-message.json](tickets/closed/T-011-sync-l3-called-every-message.json) | closed | Yes — [tools_memory.py:302-305](tools/tools_memory.py#L302-L305) TTL gate | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-012-message-truncation-orphans-tool-result.json](tickets/closed/T-012-message-truncation-orphans-tool-result.json) | closed | Yes — [pi_agent.py:509-520](pi_agent.py#L509-L520) walks to safe boundary | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-013-no-session-id.json](tickets/closed/T-013-no-session-id.json) | closed | Yes — `metadata.session_id` present in latest [logs/evolution.jsonl](logs/evolution.jsonl) entries | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-014-verify-write-false-confidence.json](tickets/closed/T-014-verify-write-false-confidence.json) | closed | Yes — [tools_memory.py:401-422](tools/tools_memory.py#L401-L422) checks both stores | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-015-mode-switch-handler-too-strict-natural-language.json](tickets/closed/T-015-mode-switch-handler-too-strict-natural-language.json) | closed | Yes — [pi_agent.py:344-371](pi_agent.py#L344-L371) loose matcher | AUDIT_TRAIL_KEEP |
| [tickets/closed/T-016-normie-mode-skips-self-messages.json](tickets/closed/T-016-normie-mode-skips-self-messages.json) | closed | Yes — [pi_agent.py:548-572](pi_agent.py#L548-L572) appends both turns to `self.messages` | AUDIT_TRAIL_KEEP |
| `tickets/open/` | empty directory | Open tickets currently live in [analysis/tickets.jsonl](analysis/tickets.jsonl): T-017, T-018, T-019 | — |

---

## `data/`

| File | Role | Status |
|---|---|---|
| [data/README.md](data/README.md) | Schema doc | Honestly notes "Legacy Tables (Not Used by Agent)" — already calls out the `app/state.py` divergence. CANONICAL |
| `data/pi.db` | SQLite cache, runtime artifact | Holds `l3_cache` table written by [tools_memory.py:30-48](tools/tools_memory.py#L30-L48). Gitignored |

---

## `logs/`

| File | Role | Status |
|---|---|---|
| [logs/evolution.jsonl](logs/evolution.jsonl) | Per-interaction telemetry | AUDIT_TRAIL_KEEP. Latest entries write `tools_used` correctly but never `tool_calls` (drift documented in `SCHEMA_MISMATCHES.md`) |
| [logs/patterns.jsonl](logs/patterns.jsonl) | Per-tool success/duration | AUDIT_TRAIL_KEEP. Working — last entries show `tool_memory_read` patterns logged with durations |
| [logs/last_review.json](logs/last_review.json), [logs/last_review.txt](logs/last_review.txt) | Monthly self-review markers | Written by [pi_agent.py:657-714](pi_agent.py#L657-L714). CANONICAL but mild duplication (one JSON, one TXT — TXT predates JSON, candidate cleanup) |

---

## Empty directories

| Path | Notes |
|---|---|
| [archive_old_docs/](archive_old_docs/) | Empty; created 2026-04-20. Could be the home of the Phase 1 archive sweep, or rolled into `docs/_archive/` per master prompt §6.1 |
| [tickets/open/](tickets/open/) | Empty — open tickets currently live in `analysis/tickets.jsonl` |
| [testing/backups/](testing/backups/), [testing/logs/](testing/logs/), [testing/results/](testing/results/) | Empty test artifact dirs — last test run produced no persisted output |
| [local_models/blobs/](local_models/blobs/) | Empty Ollama-style blob dir; only used by dead `llm/routing.py:_ask_local`. Candidate for archival once `llm/` is archived |

---

## Top-level summary

- **5 STALE root docs** describe a fix path the codebase did not take (the `llm/`-and-`memory/`-modules path). All are dated 2026-04-24, in chronological proximity to S-010 / S-011, but the actual fix lived in `pi_agent.py`. They should move to `docs/_archive/2026-04-25/` in Phase 1.
- **4 SUPERSEDED root docs** describe an older 5-ticket fix workflow (`FAILURE_TICKETS.txt`-era) that has been replaced by the engineering loop in `analysis/` + `solutions/` + `tickets/`. Archive in Phase 1.
- **2 CANONICAL (overclaim)** docs (`README.md`, `ABOUT.md`) need their capability tables corrected to mark items not verified by an end-to-end round-trip test as "🟡" rather than "✅".
- **All `analysis/`, `solutions/`, and closed-ticket files** are honest and up-to-date — the master prompt's claim that `analysis/` is "the most honest source of truth in the repo" holds up against the code.
