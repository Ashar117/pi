# Pi Agent - User Guide

## Starting Pi
```bash
cd E:\pi
python pi_agent.py
```

## Modes

### Normie Mode (Default)
- Uses Groq (free)
- Fast responses
- No tools (can't modify memory or execute code)
- Good for: casual chat, quick questions

### Root Mode
- Uses Claude Sonnet 4.6 (paid, ~$0.003/message)
- All tools enabled
- Can: store memories, execute code, modify files
- Good for: complex tasks, memory management, coding

## Commands

| Command | What it does |
|---------|-------------|
| `root mode` | Enable Claude + tools |
| `normie mode` | Switch to Groq (free) |
| `research mode` | Start 3-agent debate (Claude + Gemini + Groq) |
| `analyze performance` | Show stats from last 7 days |
| `exit` | Shut down agent |

## Tool Usage (Root Mode Only)

### Memory
```
Ash: remember I'm working on GNN paper until March 15
Pi: [Stores in L3 with expiry]

Ash: what am I working on?
Pi: [Retrieves from L3] You're working on a GNN paper...
```

### Code Execution
```
Ash: calculate the first 10 Fibonacci numbers
Pi: [Executes Python] 1, 1, 2, 3, 5, 8, 13, 21, 34, 55
```

### File Operations
```
Ash: read the first 10 lines of config.py
Pi: [Shows file contents]
```

## Memory Tiers

**L3 (Active Context):**
- Loaded on every startup
- Max 800 tokens
- Use for: current projects, active reminders
- Can expire (set expiry date)

**L2 (Organized Knowledge):**
- Searchable archive
- Categorized (Projects/Technical/People/etc.)
- Use for: permanent knowledge, decisions made

## Monthly Self-Review
Pi automatically checks for performance reviews every 30 days.
- Analyzes success rates
- Identifies patterns
- Proposes consciousness improvements
- You approve/reject changes

## Cost Management

Target: <$10/month

| Mode | Cost |
|------|------|
| Normie | $0 (free tier) |
| Root | ~$0.003 per message |
| Research | ~$0.02 per 2-round debate |

Check costs: type `analyze performance`
