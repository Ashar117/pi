# Pi — Autonomous Intelligence Agent

Pi is an evolving autonomous agent system built on Claude Sonnet 4.6 with a continuous engineering loop: build → test → ticket → run → inspect → detect → build again.

**New here?** Read [ABOUT.md](ABOUT.md) for the why, the vision, and where this project is going. The README below is the technical reference.

## Architecture

**Three Modes:**
- **Root**: Claude Sonnet 4.6 + full tool access (memory, execution, files)
- **Normie**: Groq Llama 3.3 70B (free, no tools)
- **Research**: 3-agent debate mode for complex questions

**Three-Tier Memory:**
- **L3** (Active): SQLite cache + Supabase, fast context injection, 5min sync TTL
- **L2** (Organized): Structured long-term memory with categories and metadata
- **L1** (Raw Archive): Full session transcript in raw_wiki for replay and analysis

**Tools:**
- `memory_read`, `memory_write`, `memory_delete` — structured memory operations
- `execute_python`, `execute_bash` — local execution with sandboxing
- `read_file`, `modify_file`, `create_file` — file operations with auto-logging

## Engineering Loop

Every bug, fix, and lesson is tracked:
- **Tickets** (`tickets/`) — structured problem reports with reproduction steps
- **Solutions** (`solutions/SOLUTIONS.jsonl`) — what worked, what didn't, and why
- **Lessons** (`solutions/LESSONS.md`) — synthesized patterns and rules
- **Conversation Analysis** (`analysis/`) — raw chat logs become tickets via a structured workflow; behavior-driven failures feed the same loop as crash-driven ones

## Quick Start

1. Set up environment variables in `app/config.py`:
   - `ANTHROPIC_API_KEY` — Claude Sonnet 4.6
   - `GROQ_API_KEY` — Groq Llama 3.3 70B
   - `SUPABASE_URL`, `SUPABASE_KEY` — Supabase project

2. Run the agent:
   ```bash
   python pi_agent.py
   ```

3. Commands:
   - `root mode` / `normie mode` — switch modes
   - `research mode` — debate 3 agents on a question
   - `analyze performance` — 7-day performance report
   - `exit` — shutdown with session summary

## Testing

```bash
cd testing
python run_all_tests.py
```

Covers memory, persistence, mode switching, and integration scenarios.

## Architecture Documents

- [ABOUT.md](ABOUT.md) — project intro, motivation, vision, and roadmap
- [ARCHITECTURE_DIRECTION.md](ARCHITECTURE_DIRECTION.md) — canonical design decisions, engineering loop flow
- [LESSONS.md](solutions/LESSONS.md) — collected wisdom from failures and fixes
- [SOLUTIONS.jsonl](solutions/SOLUTIONS.jsonl) — complete record of problems solved
- [analysis/README.md](analysis/README.md) — how conversation logs become tickets

## Key Principles

- **Zero hallucination**: Verify before acting, test before claiming success
- **Immutable logs**: Every session trace is preserved for analysis and learning
- **Structured memory**: Not scattered notes — searchable, categorized, with metadata
- **Cost-conscious**: Daily budgets, mode switching on cost limits, free tier when possible
- **Observable**: Every tool call, every error, every decision is logged

## Current Status

- ✅ Core agent loop (agentic while loop with proper tool use)
- ✅ Three-tier memory with Supabase backend
- ✅ Tool execution and memory operations
- ✅ Evolution tracking and performance analysis
- ✅ Session persistence and continuity
- ✅ Testing framework (Phase 1)
- ✅ Ticket and solution tracking
- 🚧 Autonomous ticket generation from failures
- 🚧 Self-improvement loop (Pi reads solutions before fixing)
- 🚧 Weak output detection and flagging

## License

MIT — See [LICENSE](LICENSE)

## Author

Built by Ash. Continuous evolution enabled.
