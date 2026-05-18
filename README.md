# Pi — Autonomous Intelligence Agent

Pi is a self-improving agent system built on Claude Sonnet 4.6 + Groq, with a continuous engineering loop:

> build → test → ticket → run → inspect → detect → build again

Every fix produces a ticket. Every ticket produces a solution record. Every recurring failure becomes a lesson. The goal: Pi runs the engineering loop on its own.

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in API keys (see below)
# run SUPABASE_SETUP.sql in your Supabase SQL editor
python pi_agent.py
```

**Required keys** (`.env`): `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`

**Optional**: `GEMINI_API_KEY` (research mode 3rd agent), `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (sprint runner escalation + Pi-to-phone messages)

Full setup: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)

---

## Modes

| Mode | Model | Cost | Use for |
| --- | --- | --- | --- |
| **root** | Claude Sonnet 4.6 | ~$0.003–0.01/msg | Code edits, file ops, full 51-tool loop |
| **normie** | Groq Llama 3.3 70B | Free | Fast chat, no tools |
| **research** | Claude + Groq + Gemini | ~$0.02/run | Hard questions, multi-agent debate |

Switch by typing: `root mode`, `normie`, `research mode`.

---

## Tools — 74 total (root mode)

Auto-regenerated section in [PI.md §7](PI.md) is the source of truth. Snapshot:

| Category | Tools |
| --- | --- |
| **Memory** (4) | `memory_read` · `memory_write` · `memory_delete` · `memory_search_semantic` *(Gemini-cosine retrieval)* |
| **Execution** (5) | `execute_python` · `execute_bash` · `read_file` · `modify_file` · `create_file` |
| **Awareness** (5) | `get_weather` · `get_news` · `get_stocks` · `get_tech_updates` · `refresh_awareness` |
| **Project** (6) | `search_codebase` · `create_ticket` · `get_session_stats` · `system_introspect` · `repo_map` · `reflect` |
| **Web** (8) | `web_search` · `web_browse` · `reddit_browse` · `reddit_search` · `reddit_thread` · `scholar_search` · `discord_read` · `daily_briefing` |
| **Obsidian** (4) | `obsidian_read` · `obsidian_write` · `obsidian_append` · `obsidian_search` |
| **Image** (1) | `image_gen` |
| **Gmail** (4) | `gmail_inbox` · `gmail_search` · `gmail_read` · `gmail_send` |
| **Calendar** (5) | `calendar_today` · `calendar_upcoming` · `calendar_search` · `calendar_create` · `calendar_delete` |
| **Documents** (6) | `read_document` · `analyze_image` · `analyze_images` · `analyze_video` · `ocr_image` · `analyze_document_smart` |
| **Faces** (4) | `detect_faces` · `recognize_face` · `register_face` · `list_registered_faces` |
| **Output** (2) | `speak` · `telegram_send` |
| **Voice** (2) | `listen` · `transcribe_file` |
| **Browser** (8) | `browser_open` · `browser_screenshot` · `browser_click` · `browser_fill` · `browser_get_text` · `browser_close` · `browser_evaluate` · `browser_wait` |
| **Watchers** (4) | `watcher_add` · `watcher_list` · `watcher_remove` · `watcher_status` |
| **Computer Use** (6) | `computer_screenshot` · `computer_click` · `computer_type` · `computer_key` · `computer_scroll` · `computer_run_task` |

Each tool is a `ToolSpec` registered with `agent/tools.py` — adding a new tool is one entry in the owning module's `TOOLS = [...]` list (see [docs/adr/002-tool-registry-pattern.md](docs/adr/002-tool-registry-pattern.md)).

---

## Memory architecture

Three tiers backed by Supabase + SQLite:

| Tier | Store | Contents | Lifetime |
| --- | --- | --- | --- |
| **L1** `raw_wiki` | Supabase | Full conversation log, every turn, both modes | 30-day prune |
| **L2** `organized_memory` | Supabase | Distilled durable facts, populated by Groq at session-end | Permanent |
| **L3** `l3_cache` | SQLite | Fast-recall ambient context, injected into system prompt | Rolling |

Every turn (all modes, all return paths) also logs locally to `logs/turns.jsonl` — durable, offline-safe.

---

## Vault / Obsidian integration

`vault/` is a local Obsidian-compatible knowledge base that mirrors session state.

```text
vault/
  notes/            ← agent-written notes (tickets, status, sprints, retros)
  memory/           ← L2/L3 snapshots  [gitignored]
  notes/per-ticket/ ← one distilled brief per ticket  [gitignored]
```

**How it works:**

- Pi can read/write vault notes during a session via `obsidian_read/write/append/search` tools
- `sync_vault()` runs at session exit — one-way push from Supabase into `vault/`
- MCP Obsidian server (`tools/mcp_obsidian_server.py`) available as an alternative real-time bridge
- **VS Code graph view:** install the [Foam](https://foamresearch.io) extension (see [docs/vscode-setup.md](docs/vscode-setup.md)) for backlinks + graph across `PI.md`, `vault/`, `CHECKPOINTS/`, `docs/`

---

## Autonomy loop (Phase 7)

```bash
python scripts/sprint.py --dry-run          # plan next ticket, no edits
python scripts/sprint.py --auto-implement   # full autonomous run
python scripts/plan_sprint.py               # Monday: set week goal in PI.md §3
python scripts/retro.py --stdout            # Friday: aggregate week stats
python scripts/refresh_pi.py               # regenerate PI.md auto-sections
```

`sprint.py` picks the highest-priority open ticket, runs Claude with the full tool loop, blocks edits to risk-flagged components without a diff-first gate, runs `verify.py`, commits to a branch, escalates via Telegram on failure. Refuses any ticket touching `tickets/god/` / `vault/.god/` / `prompts/god_consciousness.txt` / `data/god_memory.db` / `agent/god.py` (R5, ADR / [PI.md §10](PI.md)).

---

## Hardening Track (Phase 8.5)

Structural refactor track between Phase 8 (Voice) and Phase 9 (Distributed). R1–R10 closes the architectural debt accumulated through vibe-coding, with one R-ticket per week + ADRs.

| R# | Ticket | Status | What |
| --- | --- | --- | --- |
| R1 | [T-082](tickets/closed/T-082-r1-god-mode-collapse.json) | ✅ | God mode → `ModeConfig` instance; `agent/god.py` archived; unified `_respond_via_config`. [ADR-001](docs/adr/001-god-as-mode-config.md) |
| R2.1 | [T-083](tickets/open/T-083-r2-tool-registry-and-consolidation.json) | ✅ partial | 74 tools migrated to `ToolSpec` registry; `agent/tools.py` 1681→235 lines. R2.2/R2.3 deferred. [ADR-002](docs/adr/002-tool-registry-pattern.md) |
| R3 | [T-084](tickets/closed/T-084-r3-router-tier-and-tpd-budget.json) | ✅ | `LLMRouter` tier matrix (private/premium/balanced/cheap/fast) + per-provider TPD-budget brownout. [ADR-003](docs/adr/003-router-tier-and-tpd-budget.md) |
| R4 | [T-085](tickets/closed/T-085-r4-resumable-session-exit.json) | ✅ | Session exit ≤3 ops, resumable via `data/session_exit_state.json`. 5 ops moved to mid-session/cron. [ADR-005](docs/adr/005-resumable-exit.md) |
| R5 | [T-086](tickets/closed/T-086-r5-sprint-god-isolation.json) | ✅ | `sprint.py` refuses god tickets; AST-checked interactive-only god entry |
| R6 | [T-087](tickets/open/T-087-r6-partition-recovery-prework.json) | open · 30m | partition-recovery pre-work |
| R7 | [T-088](tickets/closed/T-088-r7-archive-selfmodifier.json) | ✅ | Phase-5 SelfModifier class archived |
| R8 | [T-089](tickets/open/T-089-r8-modeconfig-dataclass.json) | 🟡 Stage A | `ModeConfig` 5 new behavior fields wired into all 3 response paths. [ADR-004](docs/adr/004-modeconfig-unifies-response-paths.md) |
| R9 | [T-090](tickets/open/T-090-r9-dropped-log-local-fallback.json) | open · 4h | dropped-log local fallback |
| R10 | [T-091](tickets/open/T-091-r10-l3-prompt-cache-segment.json) | open · 1d | L3 prompt-cache segment |

Full plan: [docs/PI_ENGINEERING_LAYOUT.md](docs/PI_ENGINEERING_LAYOUT.md).

---

## Engineering loop

| Stage | Location |
| --- | --- |
| Open tickets | [tickets/open/](tickets/open/) |
| Closed tickets | [tickets/closed/](tickets/closed/) |
| Solutions (S-NNN) | [solutions/SOLUTIONS.jsonl](solutions/SOLUTIONS.jsonl) |
| Current sprint + state | [PI.md](PI.md) |
| Last session exit | [CHECKPOINTS/current.md](CHECKPOINTS/current.md) |
| CI | `python scripts/verify.py` |

---

## Repo map

| Path | Role |
|---|---|
| [PI.md](PI.md) | Single bootstrap doc — AI sessions start here |
| [pi_agent.py](pi_agent.py) | Agent class, tool loop, mode switching |
| [agent/](agent/) | Tool dispatch, prompt builder, turn log, startup banner |
| [tools/](tools/) | 14 tool modules |
| [prompts/consciousness.txt](prompts/consciousness.txt) | Pi's identity prompt |
| [scripts/](scripts/) | `sprint.py`, `plan_sprint.py`, `retro.py`, `refresh_pi.py`, `verify.py` |
| [vault/](vault/) | Obsidian-compatible knowledge base |
| [CHECKPOINTS/](CHECKPOINTS/) | Per-session exit states |
| [tickets/](tickets/) | Open + closed ticket queue |
| [solutions/SOLUTIONS.jsonl](solutions/SOLUTIONS.jsonl) | Append-only solution record |
| [testing/](testing/) | 29 tests across all components |
| [docs/](docs/) | Architecture, user guide, Obsidian setup |
| [docs/_archive/](docs/_archive/) | Phase-0 audit artifacts (superseded) |
| [SUPABASE_SETUP.sql](SUPABASE_SETUP.sql) | Cloud schema |

---

## Testing

```bash
python scripts/verify.py    # full suite — must say PASS before any commit
pytest testing/ -v          # individual suites
```

29 tests · 0 failures (last verify: PASS).

---

## Cost

Default daily limit: **$0.50** (`app/config.py`). At limit, root auto-switches to normie for the rest of the day.

---

## License

MIT — see [LICENSE](LICENSE).
