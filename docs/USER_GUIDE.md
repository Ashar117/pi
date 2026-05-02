# Pi — User Guide

How to run Pi, what each command actually does, and what to expect at each price point.

The authoritative source for command behaviour is [pi_agent.py:process_input](../pi_agent.py#L341-L399). If this guide and the code disagree, the code wins — file an issue.

---

## 1. Setup

### 1.1 Install dependencies

```bash
pip install -r requirements.txt
```

The full list is in [requirements.txt](../requirements.txt): `anthropic`, `groq`, `google-generativeai`, `supabase`, `python-dotenv`, `ollama` (only used by legacy `llm/routing.py` — will be removed in Phase 4).

### 1.2 Set environment variables

Copy [.env.example](../.env.example) to `.env` (kept out of git — see [.gitignore](../.gitignore)) and fill in the keys.

| Variable | Required? | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Root mode (Claude) and research mode |
| `GROQ_API_KEY` | yes | Normie mode and research mode |
| `SUPABASE_URL` | yes | All memory operations |
| `SUPABASE_KEY` | yes | All memory operations |
| `GEMINI_API_KEY` | optional | Research mode (debate falls back to 2-agent if missing) |
| `OPENWEATHER_API_KEY` | unused | Reserved; not wired |
| `DATABASE_URL` | unused | Reserved; not wired |

### 1.3 Set up the Supabase schema

Run [SUPABASE_SETUP.sql](../SUPABASE_SETUP.sql) in the Supabase SQL editor of your project. It creates `l3_active_memory`, `organized_memory`, `raw_wiki`, the necessary indexes, RLS policies, and seeds Ash's permanent profile.

### 1.4 Run Pi

```bash
python pi_agent.py
```

You'll see a health check (Supabase, SQLite, three API keys), the session ID, the loaded mode (default `normie`), and the prompt:

```
Ash:
```

---

## 2. Modes

### Normie mode (default)

- Model: Groq Llama 3.3 70B
- Tools: **none**
- Cost: $0 (free tier)
- Good for: casual chat, quick questions, drafts

In normie mode, Pi has read access to L3 active memory (loaded into the system prompt at startup) and can see the current session's conversation history. It **cannot** call any tool. If you ask it to remember something, the correct response is to tell you to switch to root mode — see the "Never Mime Tool Use" section of [prompts/consciousness.txt](../prompts/consciousness.txt).

### Root mode

- Model: Claude Sonnet 4.6
- Tools: all 8 (memory ops, code execution, file ops)
- Cost: ~$0.003 per message average; bounded by `DAILY_COST_LIMIT=$0.50` in [app/config.py:28](../app/config.py#L28)
- Good for: storing/recalling memory, running code, editing files, anything that touches state

When the daily cost limit is reached, Pi auto-switches to normie mode for the rest of the day ([pi_agent.py:402-406](../pi_agent.py#L402-L406)).

### Research mode

- Model: Claude + Gemini + Groq, 2 rounds + a synthesis pass
- Tools: none (debate orchestration only)
- Cost: ~$0.02 per 2-round debate
- Good for: complex questions where multiple perspectives matter

After research finishes, the synthesis is written to L3 with category `research_results` ([pi_agent.py:389-394](../pi_agent.py#L389-L394)).

---

## 3. Commands

These are the literal strings the runtime intercepts. Anything else is treated as a regular message and goes through the LLM in the current mode.

| What you type | What happens |
|---|---|
| `root mode` | Switch to root mode; reply: `Root mode active (Claude with tools)` |
| `normie mode` | Switch to normie mode; reply: `Normie mode active (Groq, free)` |
| `switch to root mode` / `go root` / `enter root` / `root` (≤8 words, contains the mode name + a switch signal) | Same as `root mode` — loose matcher, see S-010 |
| `switch to normie mode` / `go normie` / `normie` (same rules) | Same as `normie mode` |
| `research mode` | Prompts for a research question, runs the 3-agent debate, stores synthesis to L3 |
| `analyze performance` | 7-day performance report (interactions / success rate / mode usage / tool usage). **Note:** tool-usage analytics are currently empty due to [SCHEMA_MISMATCHES.md SM-001](../SCHEMA_MISMATCHES.md). |
| `exit` | Generate session summary via Groq, write to L3 with category `session_history`, print session cost, and quit |

### Command-recognition rules

[pi_agent.py:344-371](../pi_agent.py#L344-L371) does the parsing:

1. Lowercase the input, strip `?!.,;:` punctuation.
2. If it's ≤8 words and contains `root` or `normie`, AND any of (`mode` literal, ≤3 words, contains a switch verb), treat as a mode switch.
3. If it's exactly `analyze performance`, run the report.
4. If it's exactly `research mode`, prompt for a question.
5. If it's exactly `exit`, shut down with summary.
6. Otherwise, treat as a regular message.

The loose matcher exists because users naturally say "switch to root mode ?" or "yo go root", and a strict matcher silently dropped those — leaving the LLM to mime a mode switch in text while the agent stayed in normie. See L-009 for the full story.

---

## 4. Memory — what Pi actually remembers

Pi has three memory tiers (see [docs/ARCHITECTURE.md §3](ARCHITECTURE.md)):

- **L3 active context** is loaded at startup and refreshed every 5 minutes. Use it for current projects, active reminders, anything that should colour Pi's responses *right now*. Has a 800-token budget.
- **L2 organized memory** is searchable but not always loaded. Use it for permanent knowledge — preferences, decisions, technical configs.
- **L1 raw archive** is the full conversation history. Currently only populated by explicit writes; auto-logging is not yet implemented.

### Examples

```
Ash: root mode
Pi: Root mode active (Claude with tools)

Ash: remember I'm working on a GNN paper, deadline March 15 2026
Pi: [calls memory_write(content="GNN paper, deadline March 15 2026",
                        tier="l3", importance=8, category="active_project")]
    Stored. Deadline noted.
```

Then later (even after `exit` and restart):

```
Ash: what am I working on?
Pi: [calls memory_read(query="working on")]
    [retrieves: "GNN paper, deadline March 15 2026"]
    Your GNN paper. Deadline March 15.
```

### Known limitations

- **L2 search filters on title only.** If your stored content has distinctive keywords past the first 100 chars, searching for those keywords against L2 will miss. Workaround: write important content with the keywords up front. Permanent fix: see [SCHEMA_MISMATCHES.md SM-003](../SCHEMA_MISMATCHES.md), Phase 3 of [PI_MASTER_PROMPT.md](../PI_MASTER_PROMPT.md).
- **`memory_read` without an explicit tier excludes L1.** If you wrote something only to L1, ask Pi to search with `tier="l1"`. Tracked: T-017, [SCHEMA_MISMATCHES.md SM-004](../SCHEMA_MISMATCHES.md).
- **Memory recall isn't end-to-end tested.** The unit tests verify `MemoryTools` works in isolation; they don't verify Claude actually issues the right tool calls when asked. If recall ever fails for a fact you know was stored, file a ticket in `analysis/tickets.jsonl` — that's exactly the kind of behavioural bug the analysis pipeline is for.

---

## 5. Cost management

Default daily limit: **$0.50** ([app/config.py:28](../app/config.py#L28)). When reached, root → normie auto-switch for the rest of the day.

| Mode | Cost per message |
|---|---|
| Normie (Groq) | $0 |
| Root (Claude Sonnet 4.6, no tools fired) | ~$0.003 |
| Root (with one tool round-trip) | ~$0.005–0.01 |
| Research (2-round, 3-agent) | ~$0.02 |

`analyze performance` shows your 7-day total (note: the tool-usage breakdown inside the report is silently empty — see SM-001).

Per-session cost is printed on `exit`.

---

## 6. Monthly self-review

Every 30 days from the last review, Pi prompts:

```
==============================================
  MONTHLY SELF-REVIEW DUE
==============================================
Pi has been running 30+ days. Run self-review? (yes/no):
```

If you say yes, Pi runs `analyze_performance(days=30)`, identifies improvement opportunities, and proposes a `consciousness.txt` update. Auto-application is **not implemented** — Pi prints the proposal and leaves the modification to manual review ([pi_agent.py:702-703](../pi_agent.py#L702-L703)). Decline-cooldown: 7 days.

Markers live at `logs/last_review.json` (the JSON variant; the older `logs/last_review.txt` is still on disk and harmless).

---

## 7. When something goes wrong

- **The session crashes mid-conversation.** Pi logs an error and continues from the next prompt. The interaction itself is logged with `success: false` ([pi_agent.py:418-427](../pi_agent.py#L418-L427)).
- **Pi claims it stored something but it's not there next session.** Three possibilities: (a) the LLM is *miming* the storage despite the strong "Never Mime Tool Use" prompt — paste the conversation into [analysis/chat_logs.txt](../analysis/chat_logs.txt), it's a new ticket; (b) the write went to SQLite but Supabase rejected it — check Supabase logs; (c) the L2 content is real but unreachable because of SM-003. Ask Pi to query with `tier="l2"` explicitly.
- **You hit the daily cost limit early.** Check `analyze performance`. If it looks wrong, see SM-001 — analytics are currently lying.
- **Mode switch didn't work.** First, you typed something the loose matcher missed. Try `root mode` or `normie mode` exactly. If even that fails, file a ticket.

---

## 8. Where things live

| What | Path |
|---|---|
| Entry point | [pi_agent.py](../pi_agent.py) |
| Architecture (canonical) | [docs/ARCHITECTURE.md](ARCHITECTURE.md) |
| As-of repo state | [STATUS.md](../STATUS.md) |
| Pi's identity | [prompts/consciousness.txt](../prompts/consciousness.txt) |
| Open bugs (analysis pipeline) | [analysis/tickets.jsonl](../analysis/tickets.jsonl) |
| Closed tickets | [tickets/closed/](../tickets/closed/) |
| Solutions and lessons | [solutions/SOLUTIONS.jsonl](../solutions/SOLUTIONS.jsonl), [solutions/LESSONS.md](../solutions/LESSONS.md) |
| Operating protocol for VS Code Claude | [PI_MASTER_PROMPT.md](../PI_MASTER_PROMPT.md) |
