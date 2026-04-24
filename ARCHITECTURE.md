# Pi Agent - Architecture

## Core Principle

**Intelligence in Prompt, Not Code**

Pi is an autonomous AI agent where:
- Claude (via consciousness.txt) makes ALL decisions
- Tools execute actions
- No hard-coded patterns or regex
- Self-modifying and self-improving

## System Flow

```
User Input
    ↓
pi_agent.py (PiAgent.process_input)
    ↓
consciousness.txt + L3 context → system prompt
    ↓
Claude Sonnet 4.6 receives: system + history + user message
    ↓
Claude decides: which tool(s) to use (if any)
    ↓
Tools execute: memory / execution operations
    ↓
Results fed back to Claude
    ↓
Claude generates final response
    ↓
evolution.py logs interaction
    ↓
Response to user
```

## File Responsibilities

| File | Role | Wired Into Agent |
|------|------|-----------------|
| `pi_agent.py` | Main agent loop, tool orchestration | Entry point |
| `consciousness.txt` | Pi's intelligence (system prompt) | Yes |
| `tools_memory.py` | Memory CRUD operations | Yes |
| `tools_execution.py` | Code/file operations | Yes |
| `evolution.py` | Performance tracking, self-improvement | Yes |
| `routing.py` | LLM routing (Groq/Claude/Ollama) | No (legacy) |
| `research_mode.py` | 3-agent debate system | Yes (via "research mode") |
| `config.py` | Environment variables, API keys | Yes |
| `state.py` | SQLite schema (10 tables) | No (legacy schema) |

## Memory Architecture

### Three-Tier System

**L3 (Active Context):**
- Storage: Supabase (cloud) + SQLite (local cache)
- Size: ~800 tokens max
- Purpose: Always-loaded context for current state
- Synced: Real-time (write to both, read from SQLite)

**L2 (Organized Memory):**
- Storage: Supabase only
- Size: Unlimited
- Purpose: Searchable structured knowledge
- Categories: Projects, Technical, People, Decisions, Learning, Preferences

**L1 (Raw Archive):**
- Storage: Supabase only
- Size: Rolling 30-day window
- Purpose: Complete interaction history

## Tool System

All tools return standardized JSON:
```python
{"success": True/False, "output": "...", "error": "...", "verified": True/False}
```

Available tools (root mode only):
- `memory_read(query, tier)` — search L3/L2
- `memory_write(content, tier, importance, category, expiry)` — store
- `memory_delete(target, soft)` — remove or archive
- `execute_python(code)` — run Python, return stdout/stderr
- `execute_bash(command)` — run shell command
- `read_file(path, lines)` — read file or line range
- `modify_file(path, old_str, new_str)` — string-replace in any file

## Mode System

| Mode | Model | Tools | Cost | Use Case |
|------|-------|-------|------|----------|
| Normie | Groq Llama 3.3-70b | None | $0 | Chat, questions |
| Root | Claude Sonnet 4.6 | All 7 | ~$0.003/msg | Complex tasks |
| Research | Claude + Gemini + Groq | None | ~$0.02/debate | Multi-perspective |

## Evolution System

**Tracks (logs/evolution.jsonl):**
- Every interaction: timestamp, mode, model, success/fail, tool calls, duration

**Tracks (logs/patterns.jsonl):**
- Per-tool success rates over time

**Monthly Review (auto-triggered on startup):**
1. Analyze last 30 days of logs
2. Calculate success rates per tool
3. Identify failure patterns
4. Propose consciousness.txt modifications
5. User approves → SelfModifier applies patch

## Extensibility

**Add new tool:**
1. Implement in `tools/tools_execution.py` or new `tools/tool_X.py`
2. Add tool definition to `_get_tool_definitions()` in `pi_agent.py`
3. Add execution branch to `_execute_tool()` in `pi_agent.py`
4. Document in `consciousness.txt` tool list

**Modify consciousness:**
1. Edit `prompts/consciousness.txt` manually, OR
2. Let Pi propose changes via monthly review (`SelfModifier.modify_consciousness()`)
