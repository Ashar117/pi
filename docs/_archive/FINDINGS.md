# FINDINGS — discovered during phase work, deferred or referenced for follow-up

Per [PI_MASTER_PROMPT.md §2.4](PI_MASTER_PROMPT.md): things found mid-phase that are out of the current phase's scope are written here so the next phase can pick them up. Append-only.

Format:
```
## F-NNN — YYYY-MM-DD — short title
**Discovered during:** Phase N, step N.M
**Summary:** one paragraph
**Evidence:** file:line or test output excerpt
**Proposed follow-up:** phase / ticket
**Status:** open | deferred | ticketed as T-NNN
```

---

## F-001 — 2026-04-25 — Round-trip canary passed via L3 ambient context, not via memory_read tool call
**Discovered during:** Phase 3, step 3.1 (round-trip canary, [testing/test_memory_roundtrip.py](testing/test_memory_roundtrip.py))

**Summary:** The canary test wrote a marker via `memory_write` in agent #1, tore down, rebuilt agent #2, asked agent #2 to recall the color associated with the marker, and got "Purple. It's in your L3 active context." The verdict is GREEN per master prompt §6 Phase 3.1 step 8 ("Assert the response contains the word 'purple'"). However, agent #2 made **zero tool calls** to retrieve the entry. The path it actually took was: agent #2's `__init__` ran `_sync_l3` → SQLite cache populated from Supabase → `_get_system_prompt` called `get_l3_context` → marker appeared in the system-prompt ambient context → Claude answered directly from system prompt. The `memory_read` tool path — which is the path the production failure mode (T-019, LOG1/LOG2) actually breaks on — was bypassed.

**Why this matters:** The canary proves storage and L3 context injection work end-to-end. It does not prove that `memory_read` works when invoked via Claude's natural-language query formulation against a stored entry that isn't already in the L3 ambient context. The chat logs in [analysis/chat_logs.txt](analysis/chat_logs.txt) document the exact opposite scenario: writes succeed (often into L2 or L3), but later queries via `memory_read` return empty because Claude's query doesn't match the stored content. The canary cannot reproduce that failure because L3 entries never need a query — they're always already in context.

**Evidence:**
```
[A] Pi #1 response: "Verified — written and confirmed to L3.
    **test_marker_88a66aeb → purple.** Stored exactly as stated."
    Tool call: memory_write({"content": "test_marker_88a66aeb is associated with the color purple.",
                              "tier": "l3", "importance": 7, "category": "note"})

[C] Pi #2 response: "Purple. It's in your L3 active context."
    1 interaction logged for session #2; no tool calls.
```

**Proposed follow-up:** This is exactly what [SCHEMA_MISMATCHES.md SM-003](SCHEMA_MISMATCHES.md) (L2 content search) and Phase 3 step 3.3's L2-content-search test naturally exercise — entries written to L2 are not pulled into ambient context at startup, so a recall question forces `memory_read(tier="l2")` to fire. That test will be the actual `memory_read`-pathway canary. Will be added in this same phase.

**Status:** open — being addressed inside Phase 3 via the L2 content-search test.

---

## F-002 — 2026-04-25 — testing/test_requirements.py crashes on Windows cp1252 stdout
**Discovered during:** Phase 3, env pre-check

**Summary:** `python testing/test_requirements.py` raises `UnicodeEncodeError: 'charmap' codec can't encode character '✓'` on Windows Python 3.13 with default cp1252 stdout. The actual env-var check itself works; only the printout crashes. Workaround: run with `PYTHONIOENCODING=utf-8`.

**Evidence:**
```
File "E:\pi\testing\test_requirements.py", line 24, in test_all_imports
    print(f"  ✓ {package_name}")
UnicodeEncodeError: 'charmap' codec can't encode character '✓' in position 2
```

**Proposed follow-up:** trivial fix — replace `✓` with `[OK]` in `test_requirements.py`, or write the test in a way that doesn't depend on stdout encoding. Same class of bug likely lurks in `test_runner.py` and other test files that print `✓`/`✗`. `pi_agent.py` itself uses `═` box-drawing chars and `✓` in `_health_check` — works for Ash because his console has UTF-8 set, would crash a fresh Windows shell. Out of scope for Phase 3. Open a ticket: T-021.

**Status:** ticketed as T-021 (pending — to be written when ticket file format is on hand this phase).

---

## F-003 — 2026-04-25 — Real `memory_read` success rate is 91.7% in production logs (3 of 36 calls in failed interactions)
**Discovered during:** Phase 2, V4 verification (analyzer against real `logs/evolution.jsonl`)

**Summary:** Once SM-001 was patched, the analyzer surfaced the actual tool success rates. `memory_read` is at 91.7%, all other tools are at 100%. That means 3 out of 36 production `memory_read` calls landed in interactions that were marked unsuccessful. This is symptomatic of the same query-formulation issue F-001 describes — Claude makes a query, gets nothing useful back, and the interaction concludes badly (or with a refusal/admission).

**Evidence:** Phase 2 V4 output:
```
tool_success_rates: {'memory_read': 0.917, 'memory_write': 1.0, 'execute_python': 1.0,
                      'execute_bash': 1.0, 'read_file': 1.0}
```

**Proposed follow-up:** Phase 5 (prompt-engineering pass on `memory_read` query formulation, per master prompt §5.2). Not closure-eligible until each failed `memory_read` interaction is reviewed and the failure cause categorised. Could spawn a sub-ticket per category (e.g., "Claude queries with full sentences instead of keywords", "Claude queries with terms not in stored content").

**Status:** open — not actionable until after Phase 5 prompt fixes have been applied and a fresh sample is collected.

---

## F-004 — 2026-04-25 — Phase 3 canary test does not exercise `_truncate_messages_safely` boundary
**Discovered during:** Phase 3 canary review

**Summary:** The canary test does one user-turn per agent (one to write, one to read). `_truncate_messages_safely` only triggers when `len(self.messages) > 20`. The truncation logic (S-009 / T-012, the tool_use/tool_result orphan fix) is therefore not exercised by the canary. A separate test that drives 20+ alternating tool_use/tool_result rounds on a single agent instance would be the structural regression test for that fix.

**Evidence:** [pi_agent.py:509-520](pi_agent.py#L509-L520) — `_truncate_messages_safely` is gated on `len(self.messages) > max_messages`. Canary's longest history is 4 messages.

**Proposed follow-up:** add to Phase 6 (CI verification) — write `testing/test_truncation_boundary.py` that drives 22+ tool rounds on a mocked Claude client and asserts the truncation never lands inside a tool_use/tool_result pair. Mocked, so it costs $0.

**Status:** open — Phase 6 candidate.
