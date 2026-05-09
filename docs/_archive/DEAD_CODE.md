# DEAD_CODE — files & blocks not exercised by the runtime

**Phase:** 0 — read-only audit
**Date:** 2026-04-25

Each entry includes (a) import-graph evidence, (b) runtime trace evidence where available, and (c) the question Ash must answer before archiving (per master prompt §6 Phase 0 deliverable spec).

Blast radius for archival is low — every item below moves to `_archive/code/2026-04-25/` in Phase 4 with a `README.md` explaining what moved and why. Nothing is deleted.

---

## DC-001 — `llm/routing.py` (and the empty `llm/__init__.py`)

**File:** [llm/routing.py](llm/routing.py) (6,089 bytes, mtime 2026-04-19)

**Import-graph evidence (DEAD):**
- `grep -E '^(from |import ).*llm' **/*.py` returns one line: `e:\pi\llm\routing.py:8:from app.config import (` — i.e. `llm/routing.py` imports things, but nothing imports `llm/routing.py`.
- `pi_agent.py` imports `app.config`, `anthropic`, `groq`, `tools.tools_memory`, `tools.tools_execution`, `evolution`, and (lazily inside `process_input`) `core.research_mode`. It never imports `llm.routing` or `llm.tools` or `llm.tool_executor`.

**Runtime trace evidence:** All Claude API calls in production happen at [pi_agent.py:439-446](pi_agent.py#L439-L446) and [pi_agent.py:470-476](pi_agent.py#L470-L476), and at [core/research_mode.py:31-41](core/research_mode.py#L31-L41), [core/research_mode.py:106-118](core/research_mode.py#L106-L118). `route()` in `llm/routing.py` is never called.

**What the code does (if it ran):** Defines `_ask_groq`, `_ask_local` (Ollama), `_ask_claude` (Haiku, no tools), `route()`. Hard-codes model `claude-haiku-4-6` ([llm/routing.py:139](llm/routing.py#L139)) — wrong model for the current architecture. No tools wired.

**Status in docs:** [ARCHITECTURE.md:46](ARCHITECTURE.md#L46) — "routing.py: No (legacy)".

**Question for Ash before archiving:**
> Is `llm/routing.py` reserved for a future multi-model abstraction (e.g., re-introducing Ollama / multi-provider routing)? If yes, leave in place with a `# RESERVED — future multi-provider routing layer` header. If no, archive.

**Recommendation:** Archive. The "dead model string" (`claude-haiku-4-6`) is a footgun if anything ever does start importing this file. Multi-provider routing, if needed later, can be re-introduced cleanly without inheriting this file's drift.

**Side effect of archiving:**
- `llm/__init__.py` becomes orphaned — also archive (or remove the directory once empty).
- `requirements.txt:6` (`ollama>=0.4.0`) can be removed; nothing else imports `ollama`.

---

## DC-002 — `app/state.py`

**File:** [app/state.py](app/state.py) (4,182 bytes, mtime 2026-04-04)

**Import-graph evidence (DEAD):**
- `grep -E '^(from |import ).*app\.state' **/*.py` returns no results.
- `app/state.py:7` imports `from app.config import BASE_DIR`, but nothing imports `app.state` itself.

**Runtime trace evidence:** The SQLite file `data/pi.db` is created by [tools/tools_memory.py:30-48](tools/tools_memory.py#L30-L48) (`MemoryTools._init_sqlite`), which only creates the `l3_cache` table. None of the 10 tables defined in `app/state.py:init_db()` (`users`, `devices`, `threads`, `messages`, `memories`, `documents`, `tool_runs`, `cost_log`, `settings`, `audit_logs`) are ever written or read by the runtime.

**Existing acknowledgement:** [data/README.md:35-37](data/README.md#L35-L37) explicitly: "Legacy Tables (Not Used by Agent) — These were from the old system. Safe to ignore."

**Question for Ash before archiving:**
> The 10 tables in `app/state.py` look like the schema for a multi-user / multi-device system that pre-dates the current single-user-on-Windows design. Is any of this reserved for the eventual "multi-device" or "audit-grade" extension? If yes, fold it into a new `docs/SCHEMA_FUTURE.md` and archive the implementation. If no, archive both implementation and intent.

**Recommendation:** Archive. The schema can be re-derived from this file in `_archive/code/` if a future feature needs it; meanwhile its presence creates the false impression that two parallel state stores exist.

**Verification before archive (cheap, manual):**
```sql
-- Confirm none of the legacy tables exist or have rows in the current pi.db:
SELECT name FROM sqlite_master WHERE type='table';
-- Should return: l3_cache  (and only l3_cache)
```

---

## DC-003 — `archive_old_docs/` (empty directory)

**Path:** [archive_old_docs/](archive_old_docs/) (0 entries, mtime 2026-04-20)

**Evidence:** `ls -la e:/pi/archive_old_docs/` returns just `.` and `..`.

**Question for Ash:**
> Is this the intended root-of-archive for older docs? If yes, the Phase 1 archive sweep should land here (instead of the master prompt's proposed `docs/_archive/2026-04-25/`). If no, remove.

**Recommendation:** Either (a) repurpose as the destination for the Phase 1 archive (and rename to match master prompt: `docs/_archive/2026-04-25/`), or (b) remove. Master prompt §6.1 specifies `docs/_archive/YYYY-MM-DD/`, so prefer (a) with rename.

---

## DC-004 — `local_models/blobs/` (empty directory)

**Path:** [local_models/blobs/](local_models/blobs/) (0 entries, mtime 2026-04-20)

**Evidence:** Empty. Only ever populated by Ollama (a local-model runner). Used solely by [llm/routing.py:_ask_local](llm/routing.py#L77-L116).

**Status:** If `llm/routing.py` is archived (DC-001), `local_models/` has no live code path.

**Question for Ash:**
> Is local-model fallback still on the roadmap (per `consciousness.txt` line 297-300 "Normie Mode" mentions Groq, not local)? If yes — when? If no, archive `local_models/` along with `llm/routing.py`.

**Recommendation:** Archive together with DC-001 in Phase 4.

---

## DC-005 — `evolution.py:SelfModifier` class

**File:** [evolution.py:261-356](evolution.py#L261-L356)

**Import-graph evidence (PARTIAL DEAD):** `evolution.py` itself is LIVE (imported by `pi_agent.py:29`), but `SelfModifier` is never instantiated or called by the runtime. `pi_agent.py` only uses `EvolutionTracker` from this module. `_check_monthly_review` ([pi_agent.py:657-714](pi_agent.py#L657-L714)) prints "Auto-modification not yet implemented. Manual review required" — confirming the class is reserved for a future feature.

**Question for Ash:**
> When (if ever) does `SelfModifier.modify_consciousness()` get wired up? It's the path Pi would use to apply its own monthly-review proposals.

**Recommendation:** Leave in place with a one-line `# RESERVED for autonomous monthly-review consciousness updates` comment. This is intentionally-unused future infrastructure, not legacy.

---

## DC-006 — `testing/backups/`, `testing/logs/`, `testing/results/` (all empty)

**Paths:** [testing/backups/](testing/backups/), [testing/logs/](testing/logs/), [testing/results/](testing/results/)

**Evidence:** All empty. The test runner ([testing/run_all_tests.py:14-16](testing/run_all_tests.py#L14-L16)) creates `RESULTS_DIR` on demand, so these dirs aren't strictly needed pre-existing — but they were created at some point and never used.

**Question for Ash:** N/A — purely auto-generated artefacts.

**Recommendation:** Leave alone. They cost nothing and the test runner creates the right ones at runtime.

---

## DC-007 — `__pycache__/` directories

**Paths:** `e:/pi/__pycache__/`, `e:/pi/app/__pycache__/`, `e:/pi/core/__pycache__/`, `e:/pi/llm/__pycache__/`, `e:/pi/tools/__pycache__/`

**Evidence:** Auto-generated bytecode caches.

**Recommendation:** Master prompt §2.2 explicitly permits deleting `__pycache__/`. Wait until cleanup phases — they regenerate on next run.

---

## DC-008 — `pi_dna.txt` (probable SUPERSEDED, but not yet read in full)

**File:** [pi_dna.txt](pi_dna.txt) (167KB, mtime 2026-04-19)

**Status:** Mentioned in stale fix docs (`DEPLOYMENT_PROTOCOL.txt:323`, `EXECUTIVE_SUMMARY.txt:87` reference `PI_PROJECT_DNA.txt` — same file, different name). Master prompt §6.3 says to "salvage `MODULE_TEMPLATE.py` from §18".

**Evidence:** Not opened in this audit due to size. Should be paged through in Phase 1 to confirm SUPERSEDED status and to extract any salvageable templates before archive.

**Question for Ash:**
> Anything in `pi_dna.txt` that needs to land in `docs/CONTRIBUTING.md` or `docs/templates/` before it's archived?

**Recommendation:** Phase 1 task — read in full, extract `MODULE_TEMPLATE.py` if §18 still has it, then archive the rest.

---

## DC-009 — `test_progress.txt`

**File:** [test_progress.txt](test_progress.txt) (330 bytes, mtime 2026-04-20)

**Content:** A short list of the 5 facts Ash told Pi to remember during the 2026-04-20 stress test ("Subway / Python / GNN / March 15 deadline / direct comm").

**Evidence:** Not referenced by any code or doc. Personal scratch file from the original stress test.

**Question for Ash:**
> Keep as a personal note? Move to `analysis/`? Delete?

**Recommendation:** Either move to a private location or archive. It's not useful in the repo root.

---

## Empty `__init__.py` files (intentional package markers)

These are **STUB**, not DEAD. They are kept because they tell Python the directory is a package:

- [tools/__init__.py](tools/__init__.py) — kept; `tools/` is a live package.
- [app/__init__.py](app/__init__.py) — kept; `app/` contains `config.py` (LIVE).
- [core/__init__.py](core/__init__.py) — kept; `core/` contains `research_mode.py` (LIVE).
- [llm/__init__.py](llm/__init__.py) — becomes orphaned if DC-001 is archived. Then archive too.

---

## Summary

| ID | Path | Recommendation | Phase |
|---|---|---|---|
| DC-001 | `llm/routing.py` (+ `llm/__init__.py`) | Archive | 4 |
| DC-002 | `app/state.py` | Archive | 4 |
| DC-003 | `archive_old_docs/` (empty) | Repurpose as `docs/_archive/2026-04-25/` or remove | 1 |
| DC-004 | `local_models/blobs/` | Archive with DC-001 | 4 |
| DC-005 | `evolution.py:SelfModifier` | **Keep** with reservation comment | — |
| DC-006 | `testing/{backups,logs,results}/` | Leave alone | — |
| DC-007 | `__pycache__/` everywhere | Routine cleanup | — |
| DC-008 | `pi_dna.txt` | Read & extract templates → archive | 1 |
| DC-009 | `test_progress.txt` | Move/archive | 1 |

Net Phase-1 archive set (docs + dirs): 4 stale fix docs + 4 SUPERSEDED docs + `pi_dna.txt` + `FAILURE_TICKETS.txt` + `test_progress.txt`.
Net Phase-4 archive set (code): `llm/` + `app/state.py` + `local_models/`.

Total reduction in active surface area: ~10 docs and 2 module trees, all preserved in `_archive/`.
