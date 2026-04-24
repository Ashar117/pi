# Pi — Conversation Analysis Pipeline

This folder is the feedback loop for everything that goes wrong (or weakly) in actual conversations with Pi.

## Purpose

Tests catch known bugs. Logs catch crashes. But the most useful failure signal is *real conversation*: places where Pi was confused, gave a weak answer, lost continuity, hallucinated a tool result, or refused something it should have handled. Those failures rarely look like crashes — they look like a conversation that quietly went wrong.

This pipeline turns those conversations into structured tickets that flow through the same `tickets/` → `solutions/` → `LESSONS.md` engineering loop as crash-driven bugs.

## Files

| File | Purpose |
|---|---|
| `chat_logs.txt` | Where Ash manually pastes raw conversations with Pi for analysis. Append-only. |
| `tickets.jsonl` | Tickets generated from chat log analysis. Same schema as `tickets/`. Numbered T-015+. |
| `SUMMARY.md` | Running synthesis of recurring patterns observed across logs. |
| `WORKFLOW.md` | The analysis workflow — what to look for, how to score, when to escalate. |

## Workflow at a glance

```
Ash pastes a conversation into chat_logs.txt
        ↓
Claude reads the new entry
        ↓
Scans for: weak answers, hallucinations, continuity gaps, tool misuse, refusals, drift
        ↓
For each finding → append a ticket to tickets.jsonl (T-015, T-016, ...)
        ↓
Promote validated tickets to tickets/open/ (canonical ticket store)
        ↓
Update SUMMARY.md if a pattern is recurring
        ↓
Solutions land in solutions/SOLUTIONS.jsonl
        ↓
Recurring patterns get a lesson in solutions/LESSONS.md
```

## How Ash adds a chat log

1. Open `chat_logs.txt`.
2. Paste the conversation under a new `## Session YYYY-MM-DD HH:MM` heading.
3. Optional: add a one-line note describing what felt off.
4. Tell Claude to analyze it. (Or just say "check the new chat log".)

That's it. No formatting required beyond the heading. Pi's mode, errors, tool calls — anything verbatim from the terminal — is the most useful raw material.

## Privacy boundary

| File | On GitHub? | Why |
|---|---|---|
| `chat_logs.txt` | **No** (gitignored) | Raw conversations are personal. They stay local. |
| `README.md` | Yes | Public docs. |
| `WORKFLOW.md` | Yes | Public docs. |
| `SUMMARY.md` | Yes | Pattern names only — never personal content. |
| `tickets.jsonl` | Yes | Describes Pi's failures in technical terms. Quotes are paraphrased, identifiers stripped. |

When tickets are generated from chat logs, personal content (names, topics, specific facts from the conversation) is scrubbed. See the **Privacy rule** in `WORKFLOW.md`.

## Why this lives in its own folder

The main `tickets/` directory holds bugs found through code review and crash logs. The `analysis/` folder holds bugs found through *behavioral* observation. They use the same ticket schema and feed the same solution pipeline, but the source is different — and source matters when reviewing patterns later.

See `WORKFLOW.md` for the full analysis rubric.
