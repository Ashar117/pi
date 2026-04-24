# About Pi

> Pi is not a chatbot. Pi is a system that learns to engineer itself.

---

## Why I built this

Most "AI assistants" are stateless. You talk to them, they answer, the conversation evaporates, and the next one starts from zero. That's a tool, not an agent. It also means the assistant can never *get better at being itself* — every weakness is reset every session.

I wanted something different. I wanted to build an agent that:

- **Remembers** — not just facts, but its own failures, decisions, and the reasoning behind them.
- **Notices** when it does something poorly, even when nothing crashes.
- **Treats every bug as evidence** — logged, ticketed, analyzed, fixed, and recorded as a lesson.
- **Has a stable identity** that survives across sessions, models, and rewrites.
- **Eventually improves itself** under supervision, without needing me to manually patch every failure mode.

Pi is my attempt at that. It started as a personal research project and has turned into the ground I'm using to learn what it actually takes to build a long-lived autonomous system — one that can be honestly described as *evolving*, not just *responding*.

---

## What Pi is, in one paragraph

Pi is a multi-mode agent built on Claude Sonnet 4.6, with a three-tier memory backed by Supabase and SQLite, a continuous engineering loop that turns every failure into a structured ticket and lesson, and an architecture designed so that no component requires a rewrite to grow. It can read and write its own memory, execute code locally, edit files, run multi-agent debates for hard questions, and route between a paid root model and a free fallback model based on cost. Most importantly, it keeps a permanent record of what it has done, why, and what it learned — so the next version of Pi knows things this version had to learn the hard way.

---

## Capabilities (today)

| Capability | Status | Notes |
|---|---|---|
| Three-mode routing (Root / Normie / Research) | ✅ Working | Root = Claude Sonnet 4.6 with full tools. Normie = Groq Llama 3.3 70B, free, no tools. Research = 3-agent debate. |
| Three-tier memory (L3 active / L2 organized / L1 raw archive) | ✅ Working | Supabase as durable store, SQLite as cache, TTL-based sync. |
| Tool use: memory ops, file ops, Python/bash execution | ✅ Working | Sandboxed local execution, auto-logging on file changes. |
| Session persistence and continuity | ✅ Working | Session IDs propagate to logs, L1 raw archive, and summaries. |
| Engineering loop (tickets → solutions → lessons) | ✅ Working | Manual today, structured to be auto-driven later. |
| Conversation analysis pipeline | ✅ Working | Real chat logs become tickets through `analysis/`. |
| Cost awareness and budget gating | ✅ Working | Daily limits, mode switching when over budget. |
| Health diagnostics | ✅ Working | Connection, sync, success-rate checks. |
| Autonomous ticket generation from logs | 🚧 In progress | Pi flags weak outputs; auto-conversion to tickets is next. |
| Self-improvement loop (Pi reads SOLUTIONS before fixing) | 🚧 In progress | Architecture is in place, behavior is not yet wired. |

---

## Architecture, briefly

Pi is built around one principle: **observe first, act later.**

```
build → test → create/fix ticket → run → execute → inspect output
   ↑                                                     ↓
   └────── detect failure / weakness ←───────────────────┘
```

Every component writes to logs. Every failure produces a ticket. Every fix produces a solution record. Every recurring pattern produces a lesson. Pi's *engineering biography* — its tickets, solutions, and lessons — is what eventually lets it say "I've seen this failure before, here's what we tried, here's a better approach."

The system is designed in layers:

- **Identity layer** — `consciousness.txt` and `self/` define what Pi is, versioned across changes.
- **Memory layer** — three-tier storage with strict invariants (verified writes go to the durable store, read paths must match write paths, sync is rate-limited).
- **Tool layer** — replaceable tools that all auto-log to the run record.
- **Mode layer** — routing between models without breaking session state.
- **Engineering loop layer** — tickets, solutions, lessons, diagnostics, conversation analysis.
- **Self-observation layer** — Pi can read its own architecture docs, known limitations, and past solutions.

Full design rationale is in [ARCHITECTURE_DIRECTION.md](ARCHITECTURE_DIRECTION.md). The patterns Pi has already learned the hard way are in [solutions/LESSONS.md](solutions/LESSONS.md).

---

## How this evolves

I'm following a deliberate path toward autonomy. The constraint is not technical — it's trust earned through track record.

**Phase A (now)** — Pi generates great logs. I read them and act.
**Phase B (next)** — Pi reads its own logs and proposes tickets and fixes. I approve.
**Phase C (later)** — Pi executes approved fixes within a bounded scope. I review.
**Phase D (long-term)** — Pi proposes architectural changes. I decide.

At no point does Pi modify its own identity or core files without review. That's not a technical limit — it's a design choice. An autonomous agent that earns autonomy is more interesting than one that's given it.

The historical record — every session trace, every ticket, every solution, every version of `consciousness.txt` — is preserved permanently. Six months from now, twelve months from now, that history is what makes Pi able to reason about its own development as a continuous arc rather than a series of disconnected fixes.

---

## What this project is *for*

A few overlapping things, honestly:

- **A research substrate.** I'm a CS undergrad at GSU researching graph neural networks. Pi is where I get to test ideas about memory, structured reasoning, and autonomous systems in something that runs end-to-end, not just on a benchmark.
- **An engineering exercise.** Building a system that survives its own bugs taught me more about disciplined engineering than any course has. Every entry in `LESSONS.md` is a habit I now have.
- **A long-term collaborator.** Pi is being built so I can use it. The same agent that's logging this commit is the agent that will, eventually, help debug its own descendants.
- **A portfolio of how I think.** If you're reading this, the architecture documents, tickets, and lessons are the most honest thing I can show you about the way I approach hard problems.

---

## What's intentionally not here

- **Marketing claims I can't back.** Pi is not "AGI". It is not "conscious". It is a well-engineered agent system with persistent memory and a feedback loop. That's interesting on its own.
- **A polished demo.** This is a working system, not a product. The UI is a terminal. The deployment is `python pi_agent.py`. That's a feature for now — every change is observable.
- **Personal data.** Raw conversation logs, API keys, session traces — none of that lives in this repo. The `.gitignore` is strict on purpose. Public is engineering; local is personal.

---

## Status

Continuous evolution enabled. The agent that committed yesterday's code is not the same one that's running today. The lessons file gets longer. The architecture stabilizes. The autonomy boundary moves outward, slowly, on purpose.

If you want to see what's currently broken and being worked on, read [tickets/open/](tickets/open/) and [analysis/SUMMARY.md](analysis/SUMMARY.md). If you want to see what's been learned along the way, read [solutions/LESSONS.md](solutions/LESSONS.md). Those three files are the real changelog.

---

## Author

Built by **Ash** — CS undergrad at Georgia State University, researching graph neural networks, building Pi in the hours that aren't class or research.

Reach: via GitHub issues on this repo.

Pi is MIT licensed. Use it, fork it, learn from it. If you build something with the same skeleton, I'd love to hear what you did differently.
