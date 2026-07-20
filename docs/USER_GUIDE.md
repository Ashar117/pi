# Pi — User Guide

How to run Pi, what each command actually does, and what to expect at each price point.

If this guide and the code disagree, the code wins — file a ticket. (This file was fully rewritten 2026-07-07; the previous version described the April 2026 system and is archived with the rest of the v2-era docs.)

---

## 1. Setup

### 1.1 Install

```bash
pip install -r requirements.txt
cp .env.example .env    # fill in keys
```

Run [SUPABASE_SETUP.sql](../SUPABASE_SETUP.sql) in your Supabase project's SQL editor — it creates `l3_active_memory`, `organized_memory`, `raw_wiki`, indexes, and RLS policies.

### 1.2 Environment variables

| Variable | Required? | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | root mode + research mode |
| `GROQ_API_KEY` | yes | normie mode, session distillation, research mode |
| `SUPABASE_URL` / `SUPABASE_KEY` | yes | all memory operations |
| `GEMINI_API_KEY` | optional | research-mode 3rd agent; Imagen image backend |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | optional | bidirectional Telegram peer, watcher alerts, email triage buttons |
| `PI_HTTP_TOKEN` | optional | brain-server Bearer auth (any random string; **without it the server accepts any token**) |
| `PI_HTTP_PORT` | optional | brain server port, default 7712 |
| `REPLICATE_API_TOKEN` | optional | video generation (falls back to HuggingFace without it) |

### 1.3 Run

```bash
python pi.py               # normal use — talks to the warm daemon, <200ms start
python pi.py --no-daemon   # first run, or if the daemon is dead
python pi.py --status      # daemon status
python pi.py --stop        # stop the daemon
python pi_agent.py         # plain single-process REPL (no daemon)
```

The daemon (`pi_daemon.py`) also starts the HTTP brain server automatically.

---

## 2. Modes

| Mode | Model | Tools | Cost | Use for |
|---|---|---|---|---|
| **root** (default working mode) | Claude Sonnet 4.6 | all (~75) | ~$0.003–0.01/msg | anything that touches state: memory, files, code, email, calendar |
| **normie** | Groq Llama 3.3 70B | minimal | free | quick chat, drafts |
| **research** | Claude + Groq + Gemini + Qwen | debate only | ~$0.02/run | hard questions needing multiple perspectives |

Switch by typing `root mode`, `normie`, `research mode` — the matcher is deliberately loose (`go root`, `switch to normie` also work). When the daily cost cap (`app/config.py`, default $0.50) is hit, root auto-downgrades to normie for the rest of the day.

In root mode you can also ask for a multi-agent take mid-conversation — the `deep_debate` tool runs the research debate without leaving root.

---

## 3. REPL commands

Literal strings intercepted before the LLM sees them:

| Command | What happens |
|---|---|
| `root mode` / `normie` | switch modes |
| `research mode` | prompts for a question, runs the 3-agent debate, stores the synthesis to L3 |
| `chats` | list the 10 most recent conversations |
| `resume <id>` | restore a previous conversation and continue it |
| `new chat` / `/newchat` / `/new` | start a fresh conversation with a new ID |
| `analyze performance` | 7-day report: interactions, success rate, mode + tool usage |
| `help` | list these commands |
| `exit` | session summary → L2/L3 distillation → vault sync → quit (resumable if interrupted — ADR-005) |

Anything else is a normal message in the current mode.

---

## 4. Memory — what Pi actually remembers

- **L3 (always visible)** — hot context injected into the system prompt every turn (~800-token budget). Current projects, active preferences.
- **L2 (on call)** — durable distilled facts in Supabase; Pi searches it with `memory_read` when asked. Written by Groq at session end, deduplicated on write.
- **L1 (audit trail)** — every turn, all modes, auto-logged to Supabase `raw_wiki` and locally to `logs/turns.jsonl` (offline-safe, rotated at 50MB). Pruned ~30 days server-side.

Just tell Pi things ("remember that my GNN paper deadline is March 15") in root mode — it calls `memory_write` itself. Ask ("what am I working on?") and recall-shaped turns automatically run a hybrid dense-cosine + BM25 retrieval (`MemoryTools.retrieve`) across L3+L2 before the LLM sees the message — it isn't waiting for the model to remember to call a tool, and it finds paraphrases ("which animal do we study" → a fact phrased about "zebrafish") that plain keyword search misses.

Past sessions are searchable: `recall_episode` matches against per-conversation digests, and resuming a conversation (`resume <id>`) restores its full turn history.

**Forgetting is deliberate, not accidental.** Ephemeral facts ("remember just for today...") auto-expire from phrasing alone; unused facts decay daily and soft-archive (pinned facts are immune); contradicting facts get invalidated — including implication-level contradictions ("moved to Boston" vs "apartment in Atlanta") that an LLM adjudication pass catches and a keyword scan can't. Nothing is ever hard-deleted by these mechanisms. See what was forgotten and why:

```bash
python scripts/memory_cli.py forgotten --days 7
python scripts/memory_cli.py forget "topic to forget"   # semantic — finds related memories, confirms before invalidating
```

**Honest limitations:** L3's token budget means low-importance entries can be crowded out; memory quality is monitored by the memory-pollution passive skill rather than guaranteed; explicit `memory_read` tool calls still depend on the model choosing to make them — if a stored fact ever fails to come back, that's ticket-worthy, file it.

---

## 5. Access surfaces

All surfaces share one brain — the same conversation store, the same `process_input`.

### Brain server

```bash
curl http://127.0.0.1:7712/health -H "Authorization: Bearer $PI_HTTP_TOKEN"

curl -X POST http://127.0.0.1:7712/chat \
  -H "Authorization: Bearer $PI_HTTP_TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "hello", "conv_id": "my-session"}'

curl "http://127.0.0.1:7712/chat/stream?text=hello&token=$PI_HTTP_TOKEN"   # SSE
```

Localhost-only by design. One turn at a time (FIFO lock).

### Web chat UI

Open `http://127.0.0.1:7712` while Pi runs. Paste your token once (saved to localStorage). Sidebar lists past conversations; clicking one continues it.

### Chrome extension

`chrome://extensions` → Developer Mode → Load unpacked → select `extension/`. The side panel hosts the chat; right-click any selection → "Ask Pi about this page" sends the page context.

### Telegram

With `TELEGRAM_BOT_TOKEN` set, every message to your bot runs through Pi's full tool loop. Each chat is an isolated conversation — nothing bleeds into your terminal session. Pi can react, edit its last message, and send inline buttons.

**Email triage:** with Gmail configured, the email watcher checks for unread mail and pings you in Telegram with three buttons — **Draft reply · Add to calendar · Ignore**. "Draft reply" produces a Gmail *draft* for your review; Pi cannot send mail (`gmail_send` is draft-only by construction). That's the human-in-the-loop guarantee, enforced in code rather than prompt.

### Watchers

Ask Pi to watch things: files, URLs, keywords, prices, schedules, email. The `watcher` tool manages them (`add` / `list` / `remove` / `status`); a 60-second background sweep fires alerts to Telegram.

---

## 6. Gmail & Calendar setup

Put your Google OAuth client file at `data/gmail_credentials.json`; the first `gmail_*` / `calendar_*` call opens the browser consent flow and caches the token. Scopes cover read, compose (drafts), and calendar. If a demo is coming up, exercise the token refresh *before* the demo.

---

## 7. Cost

| Mode | Cost |
|---|---|
| normie (Groq) | $0 |
| root, no tools fired | ~$0.003/msg |
| root, one tool round-trip | ~$0.005–0.01 |
| research (2-round, 3-agent) | ~$0.02 |

Daily cap $0.50 (`app/config.py`) → auto-downgrade to normie. Per-session cost prints on `exit`; `analyze performance` shows the 7-day picture.

---

## 8. When something goes wrong

| Symptom | Check |
|---|---|
| Fix "done" but is it really | `python scripts/verify.py` must print `PASS` — never pipe it (the pipe eats the exit code, T-214); or read [docs/STATUS.md](STATUS.md) |
| Pi "stored" something but it's gone next session | memory write/read divergence is the project's oldest bug class — file a ticket with the conversation; check Supabase logs |
| Telegram totally silent | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env` |
| Watcher fired but no Telegram alert | `pi_agent.py` must wire `WatcherManager` to `TelegramTools.send` (the attribute is `send`, **not** `send_message` — T-274) |
| "Pi drafted my email but never sent it" | by design — review and send from Gmail yourself |
| Mode switch didn't take | type the exact form `root mode` / `normie mode`; if that fails, file a ticket |
| Daemon weirdness after crash mid-exit | next startup auto-resumes the interrupted exit (ADR-005); `python pi.py --status` to inspect |
| Silent failures suspicion | `data/silent_failures.db` collects every swallowed exception; the silent-failure passive skill reports the counts |

---

## 9. Where things live

| What | Path |
|---|---|
| Bootstrap doc for AI sessions | [PI.md](../PI.md) |
| Architecture (canonical) | [docs/ARCHITECTURE.md](ARCHITECTURE.md) |
| What works vs what's partial | [ABOUT.md](../ABOUT.md) |
| Last verify result (machine-written) | [docs/STATUS.md](STATUS.md) |
| Pi's identity prompt | `prompts/consciousness.txt` |
| Tickets | [tickets/open/](../tickets/open/) · [tickets/closed/](../tickets/closed/) |
| Solutions & lessons | [solutions/SOLUTIONS.jsonl](../solutions/SOLUTIONS.jsonl) · [solutions/LESSONS.md](../solutions/LESSONS.md) |
