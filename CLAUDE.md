# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read [`PI.md`](PI.md) first.** It is the single bootstrap doc for any AI session — who Ash is, what's in flight, the engineering loop, the file-touch policy, and the full architecture tour. This file is the always-loaded quick reference; PI.md is the source of truth. If PI.md is missing or unreadable, that's a P0 — file a ticket and tell Ash.

---

## Ponytail mode — ALWAYS ON (passive + aggressive)

> "The best code is the code you never wrote." — ponytail by Dietrich Gebert

These rules apply to **every single edit**, unprompted, forever. They are not a checklist — they are the default posture:

- **YAGNI.** If Ash didn't ask for it, don't build it. No helper functions for hypothetical future callers. No abstractions over two similar lines.
- **Stdlib first.** Reach for a new dependency only when the stdlib genuinely can't do it. If a stdlib solution is 5 lines longer, that's still the right call.
- **One file over two.** Don't split unless the file is unreadable. Don't create a module for a single function.
- **Delete > comment out.** Dead code rots. Remove it. If it might be needed, that's what git is for.
- **No defensive clutter.** No error handling for scenarios that can't happen. No fallbacks for internal invariants. No `if TYPE_CHECKING` imports for types only used in comments.
- **Shortest passing test.** A test that proves the behaviour in 5 lines beats one that proves it in 50. No test setup that mirrors production complexity.
- **Say no by default.** When in doubt about adding something — don't. Ash can always ask for more. Excess is permanent; it never gets cleaned up.

---

## Non-negotiable rules (these override defaults)

1. **Never `git push`, `git commit`, or edit `.env` / `app/config.py` without an explicit "go"** from Ash. Don't even ask to push — wait to be told.
2. **Never delete files.** Archive to `docs/_archive/` instead. Reversibility is mandatory.
3. **Test before claiming success.** No fix is "done" until `python scripts/verify.py` reports PASS.
4. **Compact output.** No long dumps; briefing only on demand.
5. **`--no-verify` / hook bypass is forbidden.**

Full file-touch gate matrix: [PI.md §10](PI.md).

---

## Commands

```bash
# Run the agent (interactive REPL)
python pi_agent.py

# CI — syntax-check every .py + bare-except lint + all non-costly tests; writes docs/STATUS.md.
# MUST say PASS before any commit. This is the gate, not pytest.
# Also runs on GitHub Actions (.github/workflows/verify.yml) on every push.
python scripts/verify.py
python scripts/verify.py --quiet

# T-214 WARNING: NEVER pipe verify.py (e.g. "| tail -8") — the pipe returns
# tail's exit code (always 0) and masks FAIL. To see the real exit code:
python scripts/verify.py --quiet; echo "EXIT=$?"
# Or read docs/STATUS.md directly — it always reflects the true result.

# Run a single test file
python -m pytest testing/test_memory.py -v
python -m pytest testing/test_memory.py::test_name -v

# Run a test file directly (some are standalone scripts, not pytest-shaped)
python testing/test_memory.py
```

**Costly tests** (hit real Claude/Groq/Supabase APIs) are excluded from `verify.py` — see the `COSTLY_TESTS` set in [scripts/verify.py](scripts/verify.py). Run them by hand only when needed. `testing/run_all_tests.py` is an older partial harness; `verify.py` is the canonical gate.

```bash
# Autonomy loop
python scripts/sprint.py --dry-run          # plan next ticket, no edits
python scripts/sprint.py --auto-implement   # full autonomous run
python scripts/refresh_pi.py                # regenerate PI.md auto-sections (§4/§7/§8/§9)
python scripts/plan_sprint.py               # weekly goal-setting into PI.md §3
python scripts/retro.py --stdout            # weekly retrospective
```

After closing a ticket or adding a tool, run `refresh_pi.py` so the auto-generated PI.md sections stay accurate (the `<!-- BEGIN/END AUTO -->` blocks — never hand-edit those).

---

## Architecture (big picture)

Pi is a continuous engineering-loop agent (build→test→ticket→run→inspect→detect), **not** a chatbot. The pieces that require reading several files to understand:

**Modes are config, not code paths.** [pi_agent.py](pi_agent.py) holds the agent class and tool loop; a single `ModeConfig` dataclass (`agent/modes.py`) drives all response paths via `_respond_via_config`. Modes: `root` (Claude Sonnet 4.6, full tool loop — live count in PI.md §7, file edits), `normie` (Groq Llama 3.3 70B, minimal allowlist, fast chat), `research` (3-agent Claude/Groq/Gemini debate; also callable from root via `deep_debate`). Switch by typing `root mode`, `normie`, `research mode`. See ADR-001 / [ADR-004](docs/adr/004-modeconfig-unifies-response-paths.md).

**Tools are a registry, not a dispatch ladder.** Each tool is a `ToolSpec` declared in its owning `tools/tools_*.py` module's `TOOLS = [...]` list and registered through `agent/tools.py`. Adding a tool = one list entry in the owning module — do not hand-edit a central dispatch table. See [ADR-002](docs/adr/002-tool-registry-pattern.md). The 21 tool modules cover memory, web, browse, gmail, calendar, media, telegram, tts/stt, scheduler, image/video, computer-use, etc. Safety contract worth knowing: `gmail_send` only creates drafts — it never sends (T-271).

**Three-tier memory** (full detail in [PI.md §6](PI.md)):

- **L1** `raw_wiki` (Supabase) — full per-turn conversation log, pruned ~30 days.
- **L2** `organized_memory` (Supabase) — distilled durable facts; Groq writes at session-end.
- **L3** `l3_cache` (SQLite) — hot ambient context injected into the system prompt every turn (token-budgeted). `memory_read` checks L3 first, falls back to L2.
- Every turn (all modes) also logs to `logs/turns.jsonl` — durable and offline-safe. Supabase + SQLite are source of truth; `vault/` is a read cache synced one-way at session exit (`tools/tools_obsidian.py::sync_vault`).

**Retrieval & forgetting** (full detail in `docs/ARCHITECTURE.md` §4): `MemoryTools.retrieve()` fuses dense cosine (Qwen/Gemini embeddings) with BM25 across L3+L2 — wired into the turn loop, not just an on-request tool. Forgetting is four mechanisms, all soft/recoverable: expiry (explicit or auto-inferred from phrasing), neglect decay (daily, default-on), contradiction (lexical + Qwen-adjudicated for implication-level conflicts), and semantic dedup. `python scripts/memory_cli.py forgotten` shows the ledger.

**LLM routing & cost.** `core/llm_router.py` picks provider/tier with a per-provider tokens-per-day budget and brownout ([ADR-003](docs/adr/003-router-tier-and-tpd-budget.md)). Default daily spend cap is in `app/config.py`; at the limit, root auto-falls back to normie. Qwen (DashScope) is first in every tier when `QWEN_API_KEY` is set; otherwise prefer Groq (free) for batch/aggregation, reserve Claude for code precision.

**The engineering loop** (PI.md §5): ticket → test → fix → `verify.py` → append `solutions/SOLUTIONS.jsonl` → move ticket `open/`→`closed/` → `refresh_pi.py`. Tickets live in [tickets/open/](tickets/open/) and [tickets/closed/](tickets/closed/) as JSON; solutions are append-only in [solutions/SOLUTIONS.jsonl](solutions/SOLUTIONS.jsonl).

**Key files:** [pi_agent.py](pi_agent.py) (agent + loop), [agent/](agent/) (modes, prompt builder, tool dispatch, turn log, router, retention), [tools/](tools/) (tool modules), [core/](core/) (llm_router, cost_tracker, research_mode, providers), [app/config.py](app/config.py) (limits — guarded), [prompts/consciousness.txt](prompts/consciousness.txt) (~700-line identity prompt).

---

## Passive Skills (slash commands)

Read-only health checks — never auto-fix, never commit. Reports written to `reports/`. Exit codes: `0`=PASS `1`=WARN `2`=FAIL.

| Command | Script | When to use |
| --- | --- | --- |
| `/pi-passive` | first 5 below | Full health check — run any time |
| `/privacy` | `privacy_publish_guard.py` | Before any `git commit` or push |
| `/session-check` | `session_exit_protocol_checker.py` | At session end |
| `/sprint-ready` | `sprint_readiness_checker.py` | Before running `sprint.py` |
| `/doc-drift` | `doc_drift_watcher.py` | When docs feel stale |
| `/consciousness-sync` | `consciousness_capability_sync.py` | After adding new tools |

`/digest` runs all 13 passive observer skills. Scripts live in [scripts/passive/](scripts/passive/).
