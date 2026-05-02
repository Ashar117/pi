# Pi — Autonomous Intelligence Agent

Pi is an evolving autonomous agent system built on Claude Sonnet 4.6, with a continuous engineering loop:

> build → test → ticket → run → execute → inspect → detect → build again

Every fix produces a ticket, every ticket produces a solution record, every recurring failure becomes a lesson.

**New here?** Start with [ABOUT.md](ABOUT.md) for the why, then [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the how. This README is the technical map.

---

## Quick start

1. Install dependencies: `pip install -r requirements.txt`
2. Copy [.env.example](.env.example) → `.env` and fill in `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`. `GEMINI_API_KEY` is optional (research mode falls back to a 2-agent debate without it).
3. Run [SUPABASE_SETUP.sql](SUPABASE_SETUP.sql) in your Supabase SQL editor.
4. `python pi_agent.py`
5. Full operating instructions: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

---

## Architecture at a glance

**Three modes**
- **Root** — Claude Sonnet 4.6 + 8 tools (memory, execution, files). ~$0.003/msg.
- **Normie** — Groq Llama 3.3 70B, no tools. Free.
- **Research** — 3-agent debate (Claude + Gemini + Groq), 2 rounds + synthesis.

**Three-tier memory** (Supabase + SQLite cache)
- **L3** active context, ~800 tokens, loaded every session, 5-minute Supabase sync TTL.
- **L2** organized memory, unlimited, searchable by category + title (content-search is a known gap — see below).
- **L1** raw archive, threaded by `session_id`.

**Eight tools** (root mode only): `memory_read`, `memory_write`, `memory_delete`, `execute_python`, `execute_bash`, `read_file`, `modify_file`, `create_file`.

Full design rationale: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Memory invariants: [solutions/LESSONS.md](solutions/LESSONS.md) L-005 / L-006 / L-010.

---

## Engineering loop

| Stage | Where it lives |
|---|---|
| Tickets (open) | [tickets/open/](tickets/open/), candidate tickets in [analysis/tickets.jsonl](analysis/tickets.jsonl) |
| Tickets (closed, audit trail) | [tickets/closed/](tickets/closed/) |
| Solutions (S-NNN, append-only) | [solutions/SOLUTIONS.jsonl](solutions/SOLUTIONS.jsonl) |
| Lessons (L-NNN, synthesised patterns) | [solutions/LESSONS.md](solutions/LESSONS.md) |
| Conversation analysis pipeline | [analysis/](analysis/) — chat logs become tickets via [analysis/WORKFLOW.md](analysis/WORKFLOW.md) |
| Per-interaction telemetry | `logs/evolution.jsonl` (gitignored) |

The `analysis/` folder is what makes silent behaviour bugs (Pi forgot something it should have remembered, drifted between modes, mimed a tool call) become structured tickets — the kind of bug that doesn't produce a stack trace.

---

## Repo map

### Canonical (active)

| Path | Role |
|---|---|
| [pi_agent.py](pi_agent.py) | Entry point — `python pi_agent.py` |
| [tools/](tools/) | `MemoryTools`, `ExecutionTools` — wired into the runtime |
| [evolution.py](evolution.py) | Telemetry tracker + reserved `SelfModifier` |
| [core/research_mode.py](core/research_mode.py) | 3-agent debate |
| [app/config.py](app/config.py) | Env loading, model strings, daily cost limit |
| [prompts/consciousness.txt](prompts/consciousness.txt) | Pi's identity prompt |
| [prompts/system.txt](prompts/system.txt) | Base prompt for Groq + research personas |
| [SUPABASE_SETUP.sql](SUPABASE_SETUP.sql) | Authoritative cloud schema |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Canonical architecture doc |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | How to run + every command |
| [STATUS.md](STATUS.md) | One-page as-of repo state |
| [PI_MASTER_PROMPT.md](PI_MASTER_PROMPT.md) | Operating protocol for VS Code Claude during engineering work |

### Phase-0 audit deliverables

| Path | Role |
|---|---|
| [STATUS.md](STATUS.md) | One-page synthesis (start here) |
| [RECONCILIATION.md](RECONCILIATION.md) | Doc-vs-code reconciliation table |
| [FILE_INVENTORY.md](FILE_INVENTORY.md) | Per-`.py` import graph and status |
| [CONTRADICTIONS.md](CONTRADICTIONS.md) | 12 contradictions with citations |
| [DEAD_CODE.md](DEAD_CODE.md) | Dead-code candidates with import-graph evidence |
| [SCHEMA_MISMATCHES.md](SCHEMA_MISMATCHES.md) | Write/read schema drift ledger |

### Audit trail (do not modify)

[analysis/](analysis/), [solutions/](solutions/), [tickets/closed/](tickets/closed/), [logs/](logs/) (gitignored), [CHECKPOINTS/](CHECKPOINTS/), [docs/_archive/](docs/_archive/).

### Legacy / pending archive

- [llm/routing.py](llm/routing.py) — old multi-provider routing layer, no importers, model string `claude-haiku-4-6` unused at runtime. Phase-4 archive target.
- [app/state.py](app/state.py) — old 10-table SQLite schema, no importers. Phase-4 archive target. Already acknowledged in [data/README.md](data/README.md).
- [docs/_archive/2026-04-25/](docs/_archive/2026-04-25/) — 12 files archived during the Phase-1 docs collapse on this date. See the [README in that folder](docs/_archive/2026-04-25/README.md) for per-file rationale.

---

## Status

The honest, citation-backed picture is in [STATUS.md](STATUS.md). The very short version:

**Working** — agent tool loop, mode switching (including loose-matched natural variants), cross-mode continuity, session ID propagation, safe message truncation, dynamic L3 category injection, sync TTL, dual-store write verification, session summary on exit, conversation analysis pipeline.

**Broken** — evolution telemetry analytics are silently empty (the analyzer reads a field name the logger doesn't write — see [SCHEMA_MISMATCHES.md SM-001](SCHEMA_MISMATCHES.md)). `memory_read(tier=None)` excludes L1 despite docstring (T-017). L2 search filters on `title` only (SM-003). Normie mode prompt sometimes mimes tool effects (T-019).

**Unverified** — the memory round-trip via the real Claude tool loop. Existing tests cover `MemoryTools` directly but never instantiate `PiAgent` and feed input through the agent loop. Phase 3 of [PI_MASTER_PROMPT.md](PI_MASTER_PROMPT.md) closes that gap.

---

## Testing

```bash
cd testing
python run_all_tests.py
```

Five suites: requirements / memory / persistence / modes / integration. They cover storage layer correctness, file presence, and Supabase reachability. They do **not** yet verify the agent's tool loop end-to-end. See `STATUS.md` for the gap and the plan.

---

## Cost

Default daily limit: **$0.50** ([app/config.py:28](app/config.py#L28)). When reached, root mode auto-switches to normie for the rest of the day. Per-mode costs:

| Mode | ~ Cost / message |
|---|---|
| Normie | $0 |
| Root | $0.003–0.01 |
| Research (2-round) | ~$0.02 |

Run `analyze performance` for a 7-day report. Caveat: the tool-usage breakdown inside that report is currently empty due to SM-001.

---

## License

MIT — see [LICENSE](LICENSE).

## Author

Built by Ashar. Continuous evolution enabled.
