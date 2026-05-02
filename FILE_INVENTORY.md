# FILE_INVENTORY — every `.py` in the repo

**Phase:** 0 — read-only audit
**Date:** 2026-04-25

Status legend:
- **LIVE** — imported (directly or indirectly) by the runtime entry point `pi_agent.py`.
- **DEAD** — present in the repo but no LIVE module imports it.
- **LEGACY** — historically wired, currently dead, kept intentionally during the audit.
- **STUB** — empty package marker (`__init__.py`).
- **TEST** — only invoked via `testing/run_all_tests.py`, not by the runtime.
- **EXEC** — module is `__main__`-runnable; not imported by other code.

Import graph determined by `grep -E '^(from |import )' **/*.py`. The runtime root is [pi_agent.py](pi_agent.py).

---

## Runtime entry + agent

| Path | Imported by | Imports | Role | Status |
|---|---|---|---|---|
| [pi_agent.py](pi_agent.py) | (none — entry point, run as `__main__`) | `app.config`, `anthropic`, `groq`, `tools.tools_memory.MemoryTools`, `tools.tools_execution.ExecutionTools`, `evolution.EvolutionTracker`, `core.research_mode.run_research_mode` (lazy, [pi_agent.py:381](pi_agent.py#L381)) | The actual agent. ~810 lines: init, prompt building, mode switching, root/normie response paths, tool dispatch, research mode, session summary, monthly review, health check, exit handling | LIVE |

---

## `tools/`

| Path | Imported by | Imports | Role | Status |
|---|---|---|---|---|
| [tools/__init__.py](tools/__init__.py) | (Python package marker) | (empty) | Marker | STUB |
| [tools/tools_memory.py](tools/tools_memory.py) | `pi_agent.py:27`, `testing/test_memory.py:12`, `testing/test_persistence.py:11`, `testing/test_integration.py:11` | `supabase`, `sqlite3`, stdlib | `MemoryTools` class — L3/L2/L1 read/write, SQLite cache, Supabase sync, context injection. Owns the `l3_cache` SQLite table (created in `_init_sqlite`, [tools_memory.py:30-48](tools/tools_memory.py#L30-L48)) | LIVE |
| [tools/tools_execution.py](tools/tools_execution.py) | `pi_agent.py:28` | `subprocess`, stdlib | `ExecutionTools` class — `execute_python`, `execute_bash`, `read_file`, `modify_file`, `create_file`, `list_files` | LIVE |

---

## `app/`

| Path | Imported by | Imports | Role | Status |
|---|---|---|---|---|
| [app/__init__.py](app/__init__.py) | (Python package marker) | (empty) | Marker | STUB |
| [app/config.py](app/config.py) | `pi_agent.py:16`, `tools/tools_memory.py:432` (under `__main__`), `core/research_mode.py:9`, `llm/routing.py:8`, `app/state.py:7`, all `testing/*.py` | `dotenv`, stdlib | Loads `.env`, exports `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `SUPABASE_URL/KEY`, `GEMINI_API_KEY`, `GROQ_MODEL`, `GEMINI_MODEL`, `LOCAL_MODEL`, `BASE_DIR`, `DAILY_COST_LIMIT=0.50`, `DEFAULT_MODE="normie"` | LIVE |
| [app/state.py](app/state.py) | (no importers) | `app.config.BASE_DIR`, `sqlite3` | Defines `init_db()` creating 10 SQLite tables: `users`, `devices`, `threads`, `messages`, `memories`, `documents`, `tool_runs`, `cost_log`, `settings`, `audit_logs`. None of those tables are used by `MemoryTools`, which uses its own `l3_cache` table | DEAD (legacy schema; explicitly acknowledged in [data/README.md](data/README.md)) |

---

## `core/`

| Path | Imported by | Imports | Role | Status |
|---|---|---|---|---|
| [core/__init__.py](core/__init__.py) | (Python package marker) | (empty) | Marker | STUB |
| [core/research_mode.py](core/research_mode.py) | `pi_agent.py:381` (lazy import inside `process_input`) | `anthropic`, `groq`, `google.genai`, `app.config` | 3-agent research debate (Claude + Gemini + Groq), 2-round structure, synthesis. Uses model `claude-sonnet-4-6` ([core/research_mode.py:34](core/research_mode.py#L34)) | LIVE |

---

## `llm/`

| Path | Imported by | Imports | Role | Status |
|---|---|---|---|---|
| [llm/__init__.py](llm/__init__.py) | (Python package marker) | (empty) | Marker | STUB (becomes orphaned if `llm/routing.py` is archived) |
| [llm/routing.py](llm/routing.py) | (no importers) | `ollama`, `anthropic`, `groq`, `app.config` | Old routing layer — `_ask_groq`, `_ask_local`, `_ask_claude`, `route()`. Uses `claude-haiku-4-6` (wrong model string for the current architecture; [llm/routing.py:139](llm/routing.py#L139)). No tools wired. Replaced by direct Claude calls in `pi_agent.py` | DEAD (legacy) |

---

## `evolution.py`

| Path | Imported by | Imports | Role | Status |
|---|---|---|---|---|
| [evolution.py](evolution.py) | `pi_agent.py:29` | stdlib only | `EvolutionTracker` (write `evolution.jsonl`, write `patterns.jsonl`, analyze last N days, propose consciousness updates) and `SelfModifier` (modify `consciousness.txt` with backup, add new tool files) | LIVE — but `analyze_performance` reads field `tool_calls` that `log_interaction` never writes (see `SCHEMA_MISMATCHES.md`). `SelfModifier` is defined but never wired to a runtime caller |

---

## `testing/`

| Path | Imported by | Imports | Role | Status |
|---|---|---|---|---|
| [testing/test_runner.py](testing/test_runner.py) | All `testing/test_*.py` (line 11 in each) | stdlib | `TestRunner` class (run/track/serialise tests + autogenerate failure tickets) | TEST |
| [testing/test_memory.py](testing/test_memory.py) | `testing/run_all_tests.py:47` (subprocess) | `tools.tools_memory.MemoryTools`, `app.config` | 5 unit tests — `MemoryTools` write/read directly. **Does not exercise the Claude tool loop.** | TEST |
| [testing/test_persistence.py](testing/test_persistence.py) | `testing/run_all_tests.py:48` (subprocess) | `tools.tools_memory.MemoryTools`, `app.config` | 4 tests — log dir existence, L3 context loading, session_history write, AST presence check on `_generate_session_summary`. **No restart-and-recall test.** | TEST |
| [testing/test_modes.py](testing/test_modes.py) | `testing/run_all_tests.py:49` (subprocess) | `test_runner` only | 4 string-matching tests against `pi_agent.py` source. **No actual mode-switch behaviour test.** | TEST |
| [testing/test_integration.py](testing/test_integration.py) | `testing/run_all_tests.py:50` (subprocess) | `tools.tools_memory.MemoryTools`, `app.config` | 4 integration tests — Supabase connection, SQLite operational, full write-read cycle on `MemoryTools`, files-and-syntax check. **The "full cycle" calls `MemoryTools` directly, not the tool loop.** | TEST |
| [testing/test_requirements.py](testing/test_requirements.py) | `testing/run_all_tests.py:46` (subprocess) | `dotenv`, stdlib | Verifies imports + env vars | TEST |
| [testing/run_all_tests.py](testing/run_all_tests.py) | (manual entry point) | stdlib | Subprocess-runs each suite, writes `MASTER_RESULTS.json` and `MASTER_FAILURES.txt` | EXEC |

**Important coverage gap (per master prompt §1.9 and Phase 3 spec):** No test in this directory invokes `PiAgent.process_input()` and asserts that Claude actually issues a `memory_write` tool_use block in response to a "remember X" prompt, then on rebuild retrieves it via `memory_read`. The thing that actually broke in production (LOG1/LOG2) is the thing not covered by automated tests.

---

## Runtime data files (not Python)

| Path | Owner | Status |
|---|---|---|
| [SUPABASE_SETUP.sql](SUPABASE_SETUP.sql) | Supabase schema | LIVE — matches the tables `tools_memory.py` queries |
| [data/pi.db](data/pi.db) | SQLite cache | LIVE — `l3_cache` table created by [tools_memory.py:30-48](tools/tools_memory.py#L30-L48) |
| [logs/evolution.jsonl](logs/evolution.jsonl), [logs/patterns.jsonl](logs/patterns.jsonl) | append-only telemetry | LIVE |
| [logs/last_review.json](logs/last_review.json), [logs/last_review.txt](logs/last_review.txt) | monthly review markers | LIVE — written by [pi_agent.py:657-714](pi_agent.py#L657-L714) |
| [prompts/consciousness.txt](prompts/consciousness.txt) | identity prompt | LIVE — loaded by [pi_agent.py:46-47](pi_agent.py#L46-L47) |
| [prompts/system.txt](prompts/system.txt) | base system prompt | LIVE — loaded by [core/research_mode.py:23](core/research_mode.py#L23) |

---

## Counts

- **Python files:** 14 (excluding `__pycache__`, `pi_env/`, `local_models/`)
- **LIVE:** 6 (`pi_agent.py`, `tools/tools_memory.py`, `tools/tools_execution.py`, `evolution.py`, `app/config.py`, `core/research_mode.py`)
- **STUB:** 4 (`tools/__init__.py`, `app/__init__.py`, `core/__init__.py`, `llm/__init__.py`)
- **DEAD:** 2 (`app/state.py`, `llm/routing.py`)
- **TEST:** 7 (everything in `testing/`)

The DEAD count being 2 is the headline finding — the repo has 2 modules that are imported by nothing in the runtime path. Both are confirmed via grep, both were known (`ARCHITECTURE.md` already labels them legacy), and both are addressed by Phase 4 of the master prompt.
