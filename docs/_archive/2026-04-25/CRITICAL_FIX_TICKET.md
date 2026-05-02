# CRITICAL FAILURE: Memory Tools Not Wired

**Severity:** BLOCKING - Pi cannot function as designed
**Status:** OPEN
**Date:** 2026-04-24

## Problem

Pi hallucinates memory operations. It claims "I've stored..." but NEVER calls tools because tools aren't wired to the LLM API calls.

## Evidence

### Logs show hallucination pattern:
```
Ash: can u remember the following, 1) Ash likes subway...
Pi: I've stored the new information in my L3 active context.
[NO TOOL CALL]

Later...
Ash: what did i tell u to remember?
[Memory] L3 search 'Ash subway order' → 0 results
Pi: Honest answer — nothing was actually saved to memory.
```

### Code audit confirms:

**routing.py line 98-111 (_ask_claude):**
```python
message = claude_client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=1024,
    system=system_prompt,
    messages=messages
    # ❌ NO tools=[] parameter
)
```

**Same issue in:**
- `_ask_groq()` - no tools
- `_ask_local()` - no tools

## Root Cause

1. System prompt (`system.txt`) mentions NOTHING about memory tools
2. Routing layer passes ZERO tools to API calls
3. Pi was told in conversations it should have L1/L2/L3 - this polluted its expectations
4. Without tools, LLMs just roleplay having them → hallucination

## Required Fix

### Phase 1: Define tools schema
Create `llm/tools.py`:

```python
MEMORY_TOOLS = [
    {
        "name": "memory_write",
        "description": "Store information in Pi's memory system",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "tier": {"type": "string", "enum": ["l1", "l2", "l3"]},
                "category": {"type": "string"},
                "importance": {"type": "integer", "minimum": 1, "maximum": 10}
            },
            "required": ["content", "tier"]
        }
    },
    {
        "name": "memory_read",
        "description": "Search Pi's memory",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "tier": {"type": "string", "enum": ["l1", "l2", "l3"]}
            },
            "required": ["query"]
        }
    }
]
```

### Phase 2: Update routing.py

**In `_ask_claude()`:**
```python
message = claude_client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=1024,
    system=system_prompt,
    messages=messages,
    tools=MEMORY_TOOLS  # ✅ ADD THIS
)

# Handle tool calls
if message.stop_reason == "tool_use":
    for block in message.content:
        if block.type == "tool_use":
            result = execute_tool(block.name, block.input)
            # Add tool result to messages and continue
```

### Phase 3: Create tool executor

Create `llm/tool_executor.py`:
```python
from memory.sqlite_store import save_memory, search_memory

def execute_tool(tool_name: str, tool_input: dict) -> dict:
    if tool_name == "memory_write":
        return handle_memory_write(tool_input)
    elif tool_name == "memory_read":
        return handle_memory_read(tool_input)
    
def handle_memory_write(params):
    save_memory(
        content=params["content"],
        tier=params["tier"],
        category=params.get("category", "note"),
        importance=params.get("importance", 5)
    )
    return {"status": "stored", "tier": params["tier"]}

def handle_memory_read(params):
    results = search_memory(
        query=params["query"],
        tier=params.get("tier")
    )
    return {"results": results}
```

### Phase 4: Update database schema

Add `memory` table to SQLite + Supabase:
```sql
CREATE TABLE memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tier TEXT CHECK(tier IN ('l1','l2','l3')),
    category TEXT,
    importance INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);
```

## Testing checklist

- [ ] Tools schema defined
- [ ] Claude API calls include tools parameter
- [ ] Tool executor handles memory_write
- [ ] Tool executor handles memory_read
- [ ] Database schema supports memory storage
- [ ] Test: "remember my subway order" → actual `memory_write` call shown in logs
- [ ] Test: "what's my subway order?" → actual `memory_read` call + correct result
- [ ] Test cross-session: exit, restart, query memory → persists

## Notes

- Groq/Llama may not support function calling reliably → tools only for Claude (root mode)
- Ollama local models don't support tools → offline mode stays tool-free
- Update system prompt to explicitly list available tools in root mode
