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

## Tools — 51 total (root mode)

| Category | Tools |
| --- | --- |
| **Memory** | `memory_read` · `memory_write` · `memory_delete` |
| **Execution** | `execute_python` · `execute_bash` · `read_file` · `modify_file` · `create_file` |
| **Awareness** | `get_weather` · `get_news` · `get_stocks` · `get_tech_updates` · `refresh_awareness` |
| **Project** | `search_codebase` · `create_ticket` · `get_session_stats` · `system_introspect` |
| **Web** | `web_search` · `web_browse` · `reddit_browse` · `reddit_search` · `reddit_thread` · `scholar_search` · `discord_read` · `daily_briefing` |
| **Obsidian** | `obsidian_read` · `obsidian_write` · `obsidian_append` · `obsidian_search` |
| **Image** | `image_gen` |
| **Gmail** | `gmail_inbox` · `gmail_search` · `gmail_read` · `gmail_send` |
| **Calendar** | `calendar_today` · `calendar_upcoming` · `calendar_search` · `calendar_create` · `calendar_delete` |
| **Documents** | `read_document` · `analyze_image` · `analyze_images` · `analyze_video` · `ocr_image` · `analyze_document_smart` |
| **Faces** | `detect_faces` · `recognize_face` · `register_face` · `list_registered_faces` |
| **Output** | `speak` · `telegram_send` |

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

`sprint.py` picks the highest-priority open ticket, runs Claude with the full tool loop, blocks edits to risk-flagged components without a diff-first gate, runs `verify.py`, commits to a branch, escalates via Telegram on failure.

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
