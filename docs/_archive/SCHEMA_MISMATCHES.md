# SCHEMA_MISMATCHES — write/read schema drift across the system

**Phase:** 0 — read-only audit
**Date:** 2026-04-25

Each entry covers a single drift between what the system *writes* and what something else later *reads* — the most expensive class of bug because it produces no error, just silently bad data.

---

## SM-001 — `evolution.py` writes `tools_used`, `analyze_performance` reads `tool_calls`

**Severity:** P0 — silently corrupts every tool-usage analytic and self-improvement proposal.

**Write path:** [evolution.py:40-55](evolution.py#L40-L55)
```python
entry = {
    "timestamp": ...,
    "mode": ...,
    ...
    "tools_used": [tc.get("name", "") for tc in tool_calls],   # ← list of name strings only
    "user_message_length": ...,
    "metadata": metadata or {}
}
with open(self.log_path, 'a') as f:
    f.write(json.dumps(entry) + '\n')
```
The full `tool_calls` argument (a list of `{"id", "name", "input"}` dicts that `pi_agent.py` builds at [pi_agent.py:459](pi_agent.py#L459)) is **discarded**. Only the `name` strings are kept under `tools_used`.

**Read path:** [evolution.py:89-95](evolution.py#L89-L95)
```python
for interaction in interactions:
    for tool_call in interaction.get("tool_calls", []):   # ← reads "tool_calls", not "tools_used"
        tool_name = tool_call.get("name", "unknown")
        tool_usage[tool_name] += 1
        tool_success[tool_name]["total"] += 1
        if interaction["success"]:
            tool_success[tool_name]["success"] += 1
```
The analyzer iterates `interaction.get("tool_calls", [])` — which is **always empty**, because the field was never written.

**Runtime evidence:** Latest entries in `logs/evolution.jsonl` (2026-04-25):
```
{"timestamp": "...", "mode": "root", "model": "claude-sonnet-4-6",
 "success": true, ..., "tools_used": ["memory_read", "memory_read"], ...}
```
Field is consistently `tools_used: [...]`. No entry has a top-level `tool_calls` field. Therefore:
- `analyze_performance().tool_usage` → always `{}`
- `analyze_performance().tool_success_rates` → always `{}`
- `identify_improvements()` ([evolution.py:142-149](evolution.py#L142-L149)) — the "tool failure" branch (`for tool, rate in analysis.get("tool_success_rates", {}).items()`) never fires
- `_performance_report()` ([pi_agent.py:589-627](pi_agent.py#L589-L627)) prints `"Tool usage: {}"` and `"Tool success rates: {}"` regardless of actual usage
- Monthly self-review's "tool_failure" improvement type is unreachable

**Proposed fix (master prompt §6 Phase 2):** in `log_interaction`, also write the full `tool_calls`:
```python
entry = {
    ...
    "tools_used": [tc.get("name", "") for tc in tool_calls],
    "tool_calls": tool_calls,   # ← add: full list, preserved for analyzer
    ...
}
```
This keeps `tools_used` for at-a-glance display and adds `tool_calls` for the analyzer. Both fields earn their bytes.

**Test to add:** [testing/test_evolution_schema.py](testing/test_evolution_schema.py) — log 3 fake interactions (1 success, 1 failure, 1 mixed), call `analyze_performance(days=7)`, assert `tool_usage` and `tool_success_rates` are populated correctly.

**Tracking ticket:** T-020 (to be opened and immediately closed in Phase 2 per master prompt §6 Phase 2 step 9).

---

## SM-002 — `app/state.py` schema vs. `tools/tools_memory.py` schema (same SQLite file)

**Severity:** P3 — currently inert (no caller of `app.state`), but creates documentation/confusion debt.

**`app/state.py` schema** ([app/state.py:17-145](app/state.py#L17-L145)):
- `users(id, name, pin_hash, created_at)`
- `devices(id, device_name, device_type, trusted, last_seen, created_at)`
- `threads(id, title, mode, created_at, updated_at)`
- `messages(id, thread_id, role, content, model_used, tokens_used, cost_usd, created_at)`
- `memories(id, tier, content, source, importance, confirmed, created_at, expires_at)`
- `documents(id, filename, file_type, file_path, summary, indexed, created_at)`
- `tool_runs(id, tool_name, input_data, output_data, success, error_msg, duration_ms, created_at)`
- `cost_log(id, api_name, model, tokens_in, tokens_out, cost_usd, mode, created_at)`
- `settings(key, value, updated_at)`
- `audit_logs(id, event_type, detail, device_id, created_at)`

**`tools/tools_memory.py` schema** ([tools/tools_memory.py:30-48](tools/tools_memory.py#L30-L48)):
- `l3_cache(id TEXT PK, content TEXT, importance INTEGER, category TEXT, active_until TEXT, created_at TEXT)` — that's it.

**Where they meet:** Both target the same SQLite file `data/pi.db`, but only `MemoryTools._init_sqlite` is ever called (from `MemoryTools.__init__`, which `pi_agent.py` calls at [pi_agent.py:54](pi_agent.py#L54)). `app/state.py:init_db()` is never called by any production path — `app/state.py` is run-as-`__main__` only ([app/state.py:148-149](app/state.py#L148-L149)), and the runtime never imports it. Acknowledged in [data/README.md:35-37](data/README.md#L35-L37).

**Drift:**
- `app/state.py:memories` and `tools_memory.py:l3_cache` both look like memory tables but differ in keys (INTEGER PK vs. TEXT/UUID PK), columns (no category in `memories`; no `tier` in `l3_cache`), and intent.
- A single source of truth for the SQLite cache should exist; `app/state.py` should not be a candidate when someone googles "where's the memory schema."

**Proposed fix:** Phase 4 — archive `app/state.py` per `DEAD_CODE.md` DC-002. Keep `tools_memory._init_sqlite` as the single SQLite-side schema authority.

**Tracking ticket:** none yet; will be folded into the Phase-4 archive PR/ticket.

---

## SM-003 — L2 search filter (`title` only) vs. L2 storage layout (`content.text`)

**Severity:** P2 — listed as a known limitation in [ARCHITECTURE_DIRECTION.md:368-369](ARCHITECTURE_DIRECTION.md#L368-L369). Master prompt §6 Phase 3 fixes it.

**Write:** [tools/tools_memory.py:202-218](tools/tools_memory.py#L202-L218) — L2 entries store the actual content under `content.text` (a JSONB field):
```python
entry = {
    "id": entry_id,
    "category": category,
    "title": content[:100],            # ← only first 100 chars become title
    "content": {"text": content},      # ← full content in JSONB
    "importance": importance,
    "status": "active",
    "created_at": now.isoformat()
}
self.supabase.table("organized_memory").insert(entry).execute()
```

**Read:** [tools/tools_memory.py:83-93](tools/tools_memory.py#L83-L93) — L2 search only filters on `title`:
```python
builder = self.supabase.table("organized_memory").select("*")
if query:
    builder = builder.ilike("title", f"%{query}%")   # ← title-only
response = builder.order("created_at", desc=True).limit(limit).execute()
```

**Drift:** A query that matches the body but not the first 100 chars of an L2 entry returns nothing. Effectively, L2 semantic search is title-only. Confirmed by the SQL schema in [SUPABASE_SETUP.sql:36-54](SUPABASE_SETUP.sql#L36-L54): an `idx_l2_title_search` GIN index exists on the title, no index on `content`.

**Proposed fix (master prompt §6 Phase 3.3):** in `memory_read(tier="l2")`, run a second query against `content->>text` using PostgREST JSON-filter syntax, merge results, dedupe. Or — longer term — add a full-text search index on the JSONB content field.

**Test to add:** [testing/test_l2_content_search.py](testing/test_l2_content_search.py) — write to L2 with distinctive content keywords that don't appear in the first 100 chars; search by content keywords; assert found.

**Tracking ticket:** Not yet; one will be opened in Phase 3 if conservative-fix path is taken; otherwise covered in S-013/L-012.

---

## SM-004 — `memory_read(tier=None)` docstring promises L1, code excludes it (T-017)

**Severity:** P2 — docstring drift; user-visible surface area lies about its own behaviour.

**Promise:** [tools/tools_memory.py:50-61](tools/tools_memory.py#L50-L61)
```python
def memory_read(self, query: str = "", tier: Optional[str] = None, limit: int = 20) -> List[Dict]:
    """
    Search memory. Empty query returns all recent entries.

    Args:
        query: Search term (empty = return all recent)
        tier: l1/l2/l3 or None for all      ← contract says "None for all"
        limit: Max results
    """
```

**Reality:** [tools/tools_memory.py:96](tools/tools_memory.py#L96) — `if tier == "l1":` (exclusive). With `tier=None`, only the L3 branch ([tools_memory.py:64](tools/tools_memory.py#L64)) and L2 branch ([tools_memory.py:83](tools/tools_memory.py#L83)) execute. L1 is never touched.

**Proposed fix (master prompt §6 Phase 3.3, conservative path):** correct the docstring:
```
tier=None searches L3+L2 only; use tier='l1' explicitly to search the raw archive.
```

**Aggressive alternative:** change to `if tier == "l1" or tier is None:` and apply a small default limit. Reason for not doing this now: L1 full-text matching is already noisy without a better query layer, and including it broadens the surface area Phase 3 must verify. Conservative fix preferred.

**Tracking ticket:** [T-017 (open)](analysis/tickets.jsonl).

---

## SM-005 — `prompts/system.txt` claims auto-persistence; runtime does per-turn JSONL only

**Severity:** P2 — prompt-side schema drift (the prompt's "Pi DOES save every conversation" claim creates expectations the runtime doesn't currently meet).

**Source A:** [prompts/system.txt:11-13](prompts/system.txt#L11-L13)
```
Pi DOES save every conversation to a local database and cloud storage (Supabase). If asked, confirm this honestly.
Pi DOES have a permanent profile of Ash that persists across sessions.
Pi DOES summarize sessions and store them for future context.
```

**Source B (runtime):**
- Per-turn → `logs/evolution.jsonl` only ([evolution.py:54-55](evolution.py#L54-L55)). Local file. Not Supabase.
- Permanent profile → loaded as part of L3 context if seeded ([SUPABASE_SETUP.sql:99-118](SUPABASE_SETUP.sql#L99-L118)). True.
- Session summary on exit → written to L3 with `category="session_history"` ([pi_agent.py:766-777](pi_agent.py#L766-L777)). True.
- Per-turn save to Supabase / L1 raw_wiki → **not implemented** ([ARCHITECTURE_DIRECTION.md:373-375](ARCHITECTURE_DIRECTION.md#L373-L375) "L1 auto-logging not implemented").

**Drift:** Statement #1 in `system.txt` overstates persistence — it implies cloud-side per-turn capture, which the runtime does not do.

**Proposed fix:** Phase 5 prompt update — replace with accurate language:
```
Pi DOES save a per-interaction telemetry record to a local log (logs/evolution.jsonl).
Pi DOES write a session summary to L3 active memory on exit.
Pi DOES NOT yet auto-archive every turn to cloud storage; tier="l1" writes are explicit.
```

**Tracking:** Phase 5; revisit if L1 auto-logging is implemented earlier.

---

## SM-006 — `consciousness.txt` references `web_search` tool that the schema doesn't define

This is technically a doc/schema mismatch (the prompt acts as a schema for "what tools exist"). Detailed in [CONTRADICTIONS.md C-009](CONTRADICTIONS.md). Not repeated here in full; tracked there.

---

## Summary — schema mismatch ledger

| ID | Severity | Phase to fix | Description |
|---|---|---|---|
| SM-001 | P0 | 2 | `evolution.py` writes `tools_used`, reads `tool_calls`. Analytics silently empty. |
| SM-002 | P3 | 4 | `app/state.py` defines a 10-table schema none of which the runtime uses. Same `pi.db` file. |
| SM-003 | P2 | 3 | L2 stores content under `content.text`, search filters on `title` only. |
| SM-004 | P2 | 3 | `memory_read(tier=None)` docstring promises L1, code excludes it (T-017). |
| SM-005 | P2 | 5 | `prompts/system.txt` claims auto-cloud-persistence; runtime does per-turn local JSONL only. |
| SM-006 | P3 | 5 | `consciousness.txt` references a `web_search` tool that doesn't exist (cross-ref C-009). |

The single highest-impact item is **SM-001** — it's silently producing wrong analytics data feeding wrong improvement proposals. Master prompt §6 Phase 2 is dedicated to it.
