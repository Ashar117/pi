# Closed Tickets
*30 tickets - synced 2026-05-04 20:59 UTC*

| ID | Title | Sev | Solution |
|---|---|---|---|
| T-006 | self.messages cleared on every mode switch — normie amnesia + session  | P0 | S-006 |
| T-007 | Session summary never writes to memory on exit | P0 | S-006 |
| T-008 | L1 memory tier returns 'Unknown tier' error | P1 | S-007 |
| T-009 | Mode switch commands require exact string match — natural language ign | P2 | S-007 |
| T-010 | get_l3_context() silently drops session_history, research_results, fil | P0 | S-008 |
| T-011 | _sync_l3() called on every single message — full Supabase fetch per pr | P1 | S-008 |
| T-012 | self.messages[-20:] truncation can orphan tool_result blocks — API cra | P1 | S-009 |
| T-013 | No session_id — tool calls, evolution logs, L1 writes can't be correla | P2 | S-009 |
| T-014 | _verify_write() only checks SQLite — Supabase failures return verified | P1 | S-008 |
| T-015 | Mode-switch handler ignores natural language phrasing | P0 | S-010 |
| T-016 | Normie mode does not append turns to self.messages — continuity broken | P0 | S-011 |
| T-017 | memory_read with tier=None silently excludes L1 despite docstring sayi | P2 | S-013 |
| T-019 | Normie mode claims tool effects despite strict prompt | high |  |
| T-020 | evolution.py write/read schema drift — analytics silently empty since  | P0 | S-012 |
| T-021 | L2 search filters on title only; content body keywords past char 100 u | P1 | S-013 |
| T-022 | Multiple Python scripts in repo crash on default Windows cp1252 stdout | P3 | S-015 |
| T-023 | Phase 3 round-trip canary passes via L3 ambient context, not via the m | P1 | S-016 |
| T-024 | L1 (raw_wiki) is never populated automatically — per-turn conversation | P2 | S-017 |
| T-024-plan | Normie mode misfires on greetings | P0 |  |
| T-025 | Memory layer completion: L1 TTL, L3 dedup, L1->L2 distillation, row se | P2 | S-018 |
| T-025-plan | Raw provider errors leak to user | P0 |  |
| T-026 | Memory layer gaps: L2 dedup, L3 expired entry pruning, L2->L3 auto-pro | P2 | S-019 |
| T-026-plan | Active context pollution: duplicates and contradictions | P0 |  |
| T-027-plan | Memory retrieval queries use junk keywords | P1 |  |
| T-028-plan | Self-awareness answers are stale | P1 |  |
| T-029-plan | L1→L2 distillation over-eager | P1 |  |
| T-030 | Awareness snapshot underused — weather/market/news questions hit LLM i | P2 | S-027 |
| T-031 | Inferred facts persisted to L3 without explicit user confirmation | P2 | S-028 |
| T-032 | Startup import-chain hang — supabase eager import via tools_memory | P3 | S-029 |
| T-033 | Obsidian integration — vault sync + live read/write tools | P2 | S-030 |

## T-006 -- self.messages cleared on every mode switch — normie amnesia + session summary never saves

**What failed:** Switching mode (root→normie or normie→root) wiped self.messages = []. Normie had no session context. Session summary exit guard 'if self.messages:' was always False after a normie session, so summary was silently skipped.

**Fix:** Remove self.messages = [] from mode switch handlers. Change exit guard to 'if self.messages or self.history:'.

## T-007 -- Session summary never writes to memory on exit

**What failed:** On exit, '[Memory] Session summary saved' never printed. Session cost showed but no summary was written to Supabase.

**Fix:** Change guard to 'if self.messages or self.history:'. Add self.history fallback in _generate_session_summary().

## T-008 -- L1 memory tier returns 'Unknown tier' error

**What failed:** Writing to tier='l1' returned {"success": False, "error": "Unknown tier"}. L1 was architecturally defined but never implemented.

**Fix:** Add elif tier == 'l1': block in memory_write() writing to raw_wiki. Add L1 read support in memory_read().

## T-009 -- Mode switch commands require exact string match — natural language ignored

**What failed:** 'switch to normie mode' did not trigger mode switch. Claude responded as if it switched but self.mode was unchanged. Only exact 'normie mode' worked.

**Fix:** Change equality check to set membership: cmd in ('normie mode', 'switch to normie mode', 'go normie').

## T-010 -- get_l3_context() silently drops session_history, research_results, file_operations, note

**What failed:** Session summaries, research results, and file operation logs are written to L3 with categories session_history, research_results, file_operations, and note. get_l3_context() has a hardcoded sections dict with only 5 keys: permanent_profile, active_project, current_priority, timed_reminder, temporary_note. Any memory entry not in those 5 categories is silently ignored at context injection time. Pi writes these memories but they never appear in the next session's context. Session continuity is completely broken.

**Fix:** Replace hardcoded sections dict with dynamic grouping: group all rows by category, format each group as a section. This ensures any category written anywhere in the system is always returned.

## T-011 -- _sync_l3() called on every single message — full Supabase fetch per prompt

**What failed:** get_l3_context() unconditionally calls _sync_l3() at line 296. _sync_l3() does: SELECT * from l3_active_memory (full table fetch), DELETE FROM l3_cache, then re-INSERT all rows. This entire Supabase round-trip happens before every response Pi generates, including every normie-mode message. It adds latency to every single prompt and hammers Supabase unnecessarily.

**Fix:** Add self._last_sync: Optional[datetime] = None to MemoryTools.__init__. In get_l3_context(), only call _sync_l3() if _last_sync is None or age > 300 seconds. Update _last_sync after each sync.

## T-012 -- self.messages[-20:] truncation can orphan tool_result blocks — API crash risk

**What failed:** When self.messages exceeds 20 entries, _respond_root() truncates with self.messages = self.messages[-20:]. The Anthropic API requires that every tool_result message is immediately preceded by an assistant message containing the matching tool_use block. If truncation cuts the assistant tool_use message but leaves the tool_result user message, the API will return a 400 error. This is a latent crash that only triggers after a long multi-tool session.

**Fix:** Before truncating, walk forward from the truncation point to find the first index where messages[i] is a user message with string content (not tool_result). Start the slice from there.

## T-013 -- No session_id — tool calls, evolution logs, L1 writes can't be correlated within a session

**What failed:** PiAgent has no session_id. Evolution log entries have no session grouping field. L1 raw_wiki writes generate a new random thread_id per write. This means there is no way to reconstruct what happened in a single session from logs — you can't answer 'what did Pi do in the 3pm session yesterday?' without guessing from timestamps.

**Fix:** Add self.session_id = str(uuid.uuid4())[:8] in __init__(). Pass session_id to evolution.log_interaction() metadata. Pass to memory_write() as L1 thread_id via the MemoryTools constructor or as a method arg.

## T-014 -- _verify_write() only checks SQLite — Supabase failures return verified=True

**What failed:** memory_write() for L3 writes to Supabase first (in try/except that swallows exceptions), then writes to SQLite, then calls _verify_write() which only checks SQLite. If Supabase write fails silently, the SQLite write still succeeds, and _verify_write() returns {verified: True}. Pi believes the memory is saved across sessions when it was actually only saved in the local cache. On next startup, _sync_l3() wipes SQLite and repopulates from Supabase — the entry disappears.

**Fix:** In _verify_write() for tier=l3, also query Supabase: self.supabase.table('l3_active_memory').select('id').eq('id', entry_id).execute(). Only return verified=True if both checks pass.

## T-015 -- Mode-switch handler ignores natural language phrasing

**What failed:** Strict equality matcher accepted only the exact strings 'root mode' / 'switch to root mode' / 'go root'. Any natural variant fell through to the LLM, which then role-played the mode switch in text while the agent stayed in normie mode.

**Fix:** Loose matcher: short message (<= 8 words after stripping punctuation) containing 'root mode' / 'normie mode' triggers the switch. Other commands stay strict.

## T-016 -- Normie mode does not append turns to self.messages — continuity broken on mode switch back to root

**What failed:** Normie mode wrote conversation turns only to self.history (a string-only research-mode helper) and skipped self.messages entirely. When mode flipped back to root, Claude received a message list missing every normie turn, so the session looked brand new from the API's perspective.

**Fix:** Append both user and assistant turns to self.messages in _respond_normie(). Reuse the safe truncation helper to keep history bounded.

## T-017 -- memory_read with tier=None silently excludes L1 despite docstring saying 'None for all'

**What failed:** Function signature and docstring promised that tier=None searches every tier. The code only searched L3+L2 in that case; the L1 branch is gated on `if tier == 'l1'` (exclusive). Pi reasons about its own capabilities from contracts (docstrings), so contract drift caused wrong self-descriptions in chat.

**Fix:** Conservative: docstring correction only (chosen). Aggressive: `if tier in ('l1', None):` with low default limit (deferred — requires faster L1 query layer).

## T-019 -- Normie mode claims tool effects despite strict prompt

**What failed:** 

**Fix:** 

## T-020 -- evolution.py write/read schema drift — analytics silently empty since inception

**What failed:** log_interaction emitted the field 'tools_used' (list of name strings). analyze_performance iterated 'interaction.get("tool_calls", [])' (list of dicts) — a field name that was never written. Result: tool_usage and tool_success_rates returned empty dicts on every call. The monthly self-review's tool-failure improvement branch was unreachable. Confirmed against the actual production log (logs/evolution.jsonl, 107 interactions across 4 sessions) — pre-fix analyze_performance returned tool_usage={}.

**Fix:** log_interaction: also write 'tool_calls' (full structured list) and 'session_id' at top level (extracted from metadata). analyze_performance: read 'tool_calls' first, fall back to materializing dicts from 'tools_used' for legacy entries (preserves analyzability of all 107 existing log entries). Add per-session breakdown reading session_id top-level first, metadata.session_id fallback.

## T-021 -- L2 search filters on title only; content body keywords past char 100 unreachable

**What failed:** memory_write(tier='l2') stores the full content under content.text (a JSONB field) but the title is only the first 100 chars of content. memory_read(tier='l2') filtered with ilike on `title` only. Distinctive keywords appearing past char 100 of the content were unreachable via L2 search — the chat-log production failure mode where Pi 'forgot' something it just stored is the same shape: write succeeds, recall returns 0 because the query doesn't hit the title bucket.

**Fix:** Two queries: ilike on title and ilike on content->>text (PostgREST JSON-path operator). Confirmed working on supabase-py 2.28.3 via direct probe before applying. Merge by id, slice to limit. Empty-query behaviour preserved.

## T-022 -- Multiple Python scripts in repo crash on default Windows cp1252 stdout when printing ✓/✗/box-drawing chars

**What failed:** On Windows Python 3.13 with default stdout encoding (cp1252), `print('✓ ...')` raises UnicodeEncodeError. Reproduced during Phase 3 env pre-check on `python testing/test_requirements.py`. The env-var checking logic itself works; only the printout crashes.

**Fix:** Applied recommendation (a) for the 6 testing/ scripts: every ✓ replaced with [OK], every ✗ replaced with [FAIL]. Recommendation (b) was already in pi_agent.py at lines 10-13 from earlier Phase 4-6 work — sys.stdout.reconfigure(encoding='utf-8', errors='replace') under hasattr guard. agent/health.py and agent/prompt.py have no __main__ block so they always run through pi_agent.py's reconfigure. Verified by Grep [✓✗] across testing/ → 0 matches; smoke test under PYTHONIOENCODING=cp1252 confirmed clean.

## T-023 -- Phase 3 round-trip canary passes via L3 ambient context, not via the memory_read tool path

**What failed:** The round-trip test wrote a marker via memory_write in agent #1, tore down, rebuilt agent #2, asked agent #2 to recall the color, and got 'Purple. It's in your L3 active context.' VERDICT: GREEN. But agent #2 made ZERO tool calls. The marker was synced from Supabase to SQLite by agent #2's _sync_l3, surfaced in get_l3_context() output, and Claude read it directly from the system prompt. The memory_read tool path — which is the path the production failure mode (T-019 / LOG1+LOG2) actually breaks on — was bypassed. The canary verifies storage and L3 ambient injection. It does NOT verify that memory_read works when invoked via Claude's natural-language query formulation against an entry that isn't already in L3 ambient context.

**Fix:** Created testing/test_memory_tool_path.py with three pytest tests that force the memory_read tool path. Key design: marker is written to L2 (organized_memory) only — L2 is never loaded into get_l3_context(), so Claude cannot read it ambiently. Fresh agent is created per test. _execute_tool is monkey-patched to capture memory_read calls. Assertions: (1) captured_queries non-empty, (2) response contains marker, (3) query contains a useful keyword (zx9/codeword/secret). Marked @pytest.mark.costly. Companion fix S-013 (T-021) had already fixed the L2 content-search to search content->>text, not just title, so the forced tool call actually returns results.

## T-024 -- L1 (raw_wiki) is never populated automatically — per-turn conversation archive not implemented

**What failed:** The architecture specifies L1 as a 'complete interaction archive' and SM-005 documents that 'L1 auto-logging not implemented.' raw_wiki is populated only when Claude explicitly calls memory_write(tier='l1') — which rarely happens and is not guaranteed. Without auto-logging, Pi has no complete interaction history, which blocks Phase B autonomy (Pi reading its own logs to generate improvement tickets). Secondarily, the existing memory_write(tier='l1') path derived thread_id from session_id (8 hex chars) but raw_wiki.thread_id is UUID NOT NULL — the insert silently failed every time.

**Fix:** Added MemoryTools.log_turn() with batch Supabase insert (user + tool + assistant rows per turn). Fixed memory_write(tier='l1') to derive thread_id via uuid5(NAMESPACE_DNS, session_id) — deterministic, valid UUID, matches log_turn rows for same session. Added self.l1_thread_id and self.turn_number to PiAgent.__init__. Wired log_turn() into _respond_root (with l1_tool_records capturing name/input/result_summary) and _respond_normie. Verified via testing/test_l1_autolog.py — 4 assertions green.

## T-024-plan -- Normie mode misfires on greetings

**What failed:** 

**Fix:** 

## T-025 -- Memory layer completion: L1 TTL, L3 dedup, L1->L2 distillation, row sequencing

**What failed:** Four gaps remain after T-024. (1) L1 grows forever — no 30-day TTL enforcement. (2) L3 accumulates duplicates — if Claude re-writes the same fact across sessions, it stacks. (3) No L1->L2 distillation — L1 is a write-only archive; memorable facts never flow automatically to L2 for future recall. (4) All rows in a log_turn() call share the same timestamp — within-turn ordering is insertion-order-only, which is fragile when Supabase returns rows in an unspecified order.

**Fix:** Added _is_l3_duplicate() (SQLite prefix check, 120 chars, same category), get_l1_thread() (fetches and sorts by turn+seq metadata), prune_l1(days=30) (deletes old raw_wiki rows) to MemoryTools. Created memory/pipeline.py with distill_session() — reads L1 thread via Groq (free), extracts importance>=4 facts, writes to L2. agent/session.py on_exit() now calls distill_session then prune_l1, both non-fatal. seq field added to log_turn() rows for stable within-turn ordering.

## T-025-plan -- Raw provider errors leak to user

**What failed:** 

**Fix:** 

## T-026 -- Memory layer gaps: L2 dedup, L3 expired entry pruning, L2->L3 auto-promotion

**What failed:** Three gaps remain after T-025. (1) L2 has no dedup — distill_session() can write the same fact to organized_memory across sessions. (2) Expired L3 entries (active_until < now) are filtered in get_l3_context() queries but never deleted from Supabase or SQLite — they accumulate silently. (3) No L2->L3 promotion pathway — high-importance facts written by distillation to L2 never automatically rise to L3 ambient context.

**Fix:** Added _is_l2_duplicate() (Supabase title prefix match, returns None on error), L2 dedup check in memory_write(tier='l2'), prune_l3_expired() (Supabase+SQLite, independent error paths), promote_l2_to_l3(threshold=8) (L2 query -> _is_l3_duplicate filter -> memory_write L3). agent/session.py on_exit() now calls promote then prune for L3 after distillation. All calls non-fatal.

## T-026-plan -- Active context pollution: duplicates and contradictions

**What failed:** 

**Fix:** 

## T-027-plan -- Memory retrieval queries use junk keywords

**What failed:** 

**Fix:** 

## T-028-plan -- Self-awareness answers are stale

**What failed:** 

**Fix:** 

## T-029-plan -- L1→L2 distillation over-eager

**What failed:** 

**Fix:** 

## T-030 -- Awareness snapshot underused — weather/market/news questions hit LLM instead of cached data

**What failed:** Normie mode called Groq (and hit rate-limit errors) for 'what's the weather' even though the awareness snapshot was already loaded at startup. Root mode only answered correctly because Claude incidentally noticed the snapshot in the system prompt — no structured shortcut existed.

**Fix:** Created agent/awareness_shortcut.py: try_answer_from_awareness(user_message, snapshot) -> Optional[str]. Pure function, no Pi imports, no side effects. Signal sets for weather/markets/news. Extractors return None on 'unavailable'. Wired into both _respond_normie and _respond_root as the first thing before _get_system_prompt. Both paths log to evolution with model='shortcut', cost=0.0, shortcircuit=True and archive to L1. _get_system_prompt now injects snapshot for both modes (was root-only).

## T-031 -- Inferred facts persisted to L3 without explicit user confirmation

**What failed:** Pi inferred 'F-1 visa' from 'I'm a student' and stored it on a one-word 'yup'. Earlier it inferred Lawrenceville was the user's home city (wrong — it's just current location). consciousness.txt had no stated-vs-inferred distinction. memory_write had no source field. Nothing blocked unconfirmed inferences from reaching L3.

**Fix:** Added `source: str = 'stated'` to MemoryTools.memory_write(). Rejection guard at top of L3 branch returns success=False with descriptive error when source='inferred_unconfirmed'. Added `source` enum field to memory_write tool schema in agent/tools.py with description guiding Claude on correct usage. Added 'INFERRED VS STATED FACTS' section to consciousness.txt with concrete rules and examples covering when one-word confirmations are acceptable, when to ask again, and that inferred_unconfirmed is tool-level blocked.

## T-032 -- Startup import-chain hang — supabase eager import via tools_memory

**What failed:** First chat run terminated with KeyboardInterrupt deep inside tenacity -> pyiceberg -> storage3 -> supabase import chain. Second run worked because the import was cached. Supabase alone takes ~1.2s to import on cold disk.

**Fix:** Moved `from supabase import create_client` from module top into MemoryTools.__init__. Updated three test helpers (test_t026_dedup_and_profile.py, test_memory_tools_gaps.py, test_inferred_facts.py) to remove the now-unnecessary `patch('tools.tools_memory.create_client')` context managers — all three use MemoryTools.__new__ which bypasses __init__, so the patch was a no-op safety net. `import pi_agent` time dropped from 1.82s to 1.21s (−0.61s, ~34%).

## T-033 -- Obsidian integration — vault sync + live read/write tools

**What failed:** No vault existed. Pi had no way to mirror memory to a human-readable format. VS Code Claude sessions had to load all docs at startup (~5K tokens overhead). The ObsidianTools class referenced in agent/tools.py did not exist.

**Fix:** Created tools/tools_obsidian.py with two responsibilities: (1) ObsidianTools class wrapping the Obsidian Local REST API (port 27123) — obsidian_read/write/append/search, all gracefully degrade when Obsidian is closed; (2) sync functions for session-exit mirroring — sync_l3_to_vault (SQLite, offline), sync_l2_to_vault (Supabase), render_tickets_to_vault (JSON->markdown tables), render_status_to_vault (docs/STATUS.md copy), all atomic (.tmp->replace) and non-fatal. Created vault/ directory structure with README.md. Wired sync_vault() into agent/session.py::on_exit() after promotion/prune. Updated .gitignore: vault/memory/ and vault/notes/per-ticket/ stay local.
