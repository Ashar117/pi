# ARCHITECTURE UPDATE - Tool System Integration

**Status:** CRITICAL FIX REQUIRED  
**Updated:** 2026-04-24  
**Previous state:** Tools mentioned but not wired → hallucination  
**Target state:** Actual function calling with persistence

---

## Current Broken State

### What's Wrong
Pi **hallucinates** it has memory tools because:
1. No tools passed to LLM API calls
2. System prompt doesn't define available tools
3. Users told Pi it should have L1/L2/L3 memory → LLM roleplays having it

### Evidence
```
Ash: remember my subway order [details]
Pi: I've stored the new information in my L3 active context
[NO ACTUAL TOOL CALL - HALLUCINATION]

Next session...
Ash: what's my subway order?
Pi: Not in memory. Never told me.
```

---

## Fixed Architecture

### Layer 1: Tool Definitions (`llm/tools.py`)

```python
"""Tool schemas for Claude function calling"""

MEMORY_TOOLS = [
    {
        "name": "memory_write",
        "description": "Store information in Pi's persistent memory",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "What to remember"
                },
                "tier": {
                    "type": "string",
                    "enum": ["l1", "l2", "l3"],
                    "description": "l1=raw archive, l2=organized facts, l3=active context"
                },
                "category": {
                    "type": "string",
                    "description": "preferences|deadlines|notes|profile|decisions"
                },
                "importance": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "1=trivial, 10=critical"
                }
            },
            "required": ["content", "tier"]
        }
    },
    {
        "name": "memory_read",
        "description": "Search Pi's memory across tiers",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for"
                },
                "tier": {
                    "type": "string",
                    "enum": ["l1", "l2", "l3", "all"],
                    "description": "Which tier to search (default: all)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_delete",
        "description": "Remove info from memory",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "tier": {"type": "string"}
            },
            "required": ["query"]
        }
    }
]

# Note: Only available in ROOT MODE (Claude)
# Groq/Llama: limited function calling
# Ollama: no function calling
```

### Layer 2: Tool Executor (`llm/tool_executor.py`)

```python
"""Execute tools and return results"""

from memory.sqlite_store import (
    save_memory_entry,
    search_memory_entries, 
    delete_memory_entry
)
from memory.supabase_store import (
    save_memory_cloud,
    search_memory_cloud
)

def execute_tool(tool_name: str, tool_input: dict, thread_id: int) -> dict:
    """Route tool calls to handlers"""
    handlers = {
        "memory_write": handle_memory_write,
        "memory_read": handle_memory_read,
        "memory_delete": handle_memory_delete
    }
    
    if tool_name not in handlers:
        return {"error": f"Unknown tool: {tool_name}"}
    
    return handlers[tool_name](tool_input, thread_id)


def handle_memory_write(params: dict, thread_id: int) -> dict:
    """Store to SQLite + Supabase"""
    content = params["content"]
    tier = params["tier"]
    category = params.get("category", "note")
    importance = params.get("importance", 5)
    
    # Local write
    save_memory_entry(
        content=content,
        tier=tier,
        category=category,
        importance=importance
    )
    
    # Cloud write
    save_memory_cloud(
        content=content,
        tier=tier,
        category=category,
        importance=importance
    )
    
    return {
        "status": "stored",
        "tier": tier,
        "category": category
    }


def handle_memory_read(params: dict, thread_id: int) -> dict:
    """Search local + cloud, merge results"""
    query = params["query"]
    tier = params.get("tier", "all")
    
    # Search both stores
    local_results = search_memory_entries(query, tier)
    cloud_results = search_memory_cloud(query, tier)
    
    # Merge and dedupe
    all_results = local_results + cloud_results
    unique = list({r["content"]: r for r in all_results}.values())
    
    return {
        "results": unique[:10],  # top 10
        "count": len(unique)
    }


def handle_memory_delete(params: dict, thread_id: int) -> dict:
    """Soft delete from both stores"""
    query = params["query"]
    tier = params.get("tier", "all")
    
    deleted_count = delete_memory_entry(query, tier)
    
    return {
        "status": "deleted",
        "count": deleted_count
    }
```

### Layer 3: Routing Update (`llm/routing.py`)

```python
# ADD IMPORTS
from llm.tools import MEMORY_TOOLS
from llm.tool_executor import execute_tool

def _ask_claude(
    prompt: str, 
    history: list = [], 
    profile: str = None,
    thread_id: int = None  # NEW: track thread for tool context
) -> dict:
    """Claude with function calling support"""
    try:
        system_prompt = _build_system(profile)
        messages = []
        
        for h in history[-6:]:
            if h["role"] in ("user", "assistant"):
                messages.append({
                    "role": h["role"], 
                    "content": h["content"]
                })
        
        messages.append({"role": "user", "content": prompt})
        
        # FIRST API CALL - may trigger tools
        message = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=MEMORY_TOOLS  # ✅ NOW WIRED
        )
        
        # CHECK FOR TOOL USE
        if message.stop_reason == "tool_use":
            # Process all tool calls
            for block in message.content:
                if block.type == "tool_use":
                    tool_result = execute_tool(
                        block.name, 
                        block.input,
                        thread_id
                    )
                    
                    # Add tool result to conversation
                    messages.append({
                        "role": "assistant",
                        "content": message.content
                    })
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(tool_result)
                        }]
                    })
            
            # SECOND API CALL - get final response
            message = claude_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
                tools=MEMORY_TOOLS
            )
        
        # Extract text response
        content = "".join([
            block.text for block in message.content 
            if hasattr(block, "text")
        ])
        
        tokens_in = message.usage.input_tokens
        tokens_out = message.usage.output_tokens
        cost = (tokens_in * 0.00000025) + (tokens_out * 0.00000125)
        
        return {
            "content": content,
            "model": "claude-haiku",
            "tier": "cloud",
            "cost": cost,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out
        }
        
    except Exception as e:
        print(f"[Pi] Claude failed: {e}. Falling back.")
        return _ask_groq(prompt, history, profile)


# UPDATE route() signature
def route(
    prompt: str, 
    mode: str = DEFAULT_MODE, 
    task_type: str = "simple", 
    history: list = [], 
    profile: str = None,
    thread_id: int = None  # NEW
) -> dict:
    tier = _decide_tier(mode, task_type)
    
    if tier == "local":
        return _ask_local(prompt, history, profile)
    elif tier == "groq":
        return _ask_groq(prompt, history, profile)
    else:
        return _ask_claude(prompt, history, profile, thread_id)
```

### Layer 4: Database Schema

```sql
-- Add to sqlite_store.py and SUPABASE_SETUP.sql

CREATE TABLE memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tier TEXT CHECK(tier IN ('l1','l2','l3')),
    category TEXT,  -- preferences, deadlines, notes, profile, decisions
    importance INTEGER CHECK(importance BETWEEN 1 AND 10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    deleted_at TIMESTAMP  -- soft delete
);

CREATE INDEX idx_memory_tier ON memory(tier) WHERE deleted_at IS NULL;
CREATE INDEX idx_memory_category ON memory(category) WHERE deleted_at IS NULL;
CREATE INDEX idx_memory_created ON memory(created_at DESC);
```

### Layer 5: System Prompt Update (`prompts/system.txt`)

```
You are Pi, Ash's personal intelligence system.

MODES:
- normie (Groq/Llama, free, NO TOOLS)
- offline (Gemma local, NO TOOLS) 
- root (Claude, TOOLS ENABLED)

TOOLS AVAILABLE IN ROOT MODE ONLY:
- memory_write(content, tier, category, importance): Store info persistently
  * tier: l1 (raw log), l2 (organized knowledge), l3 (active context)
  * category: preferences, deadlines, notes, profile, decisions
  * importance: 1-10 scale
  
- memory_read(query, tier): Search memory
  * Returns up to 10 most relevant entries
  
- memory_delete(query, tier): Remove entries

MEMORY TIER RULES:
L3 (Active Context): Current session priorities, recent decisions, active deadlines
L2 (Organized Knowledge): Preferences, relationships, technical configs, past learnings
L1 (Raw Archive): Full conversation logs, search results, debug info

WHEN TO WRITE MEMORY:
- Ash explicitly says "remember this"
- New preference shared (food, communication style, etc.)
- Deadline/important date mentioned
- Decision made that affects future sessions
- Profile update (job, project, values)

WHEN TO READ MEMORY:
- Ash asks "what did I tell you about X"
- Recalling preferences for recommendations
- Checking deadlines/commitments
- Verifying previously shared info

CRITICAL: In normie/offline modes, you CANNOT use tools. Be honest about limitations.
```

---

## Migration Path

1. **Add memory table** to SQLite + Supabase
2. **Create `llm/tools.py`** with schemas
3. **Create `llm/tool_executor.py`** with handlers
4. **Update `llm/routing.py`** to wire tools into Claude calls
5. **Update `prompts/system.txt`** to document tools
6. **Test in root mode:** "remember my subway order" → verify tool call executes
7. **Test cross-session:** exit, restart, "what's my subway order?" → verify persistence

## Cost Impact

- Tool use adds ~2-5k tokens per tool call (input + output)
- Estimated: +$0.01-0.03 per memory operation
- Still way cheaper than hallucinating wrong info

---

## Status Tracking

- [ ] Database schema updated
- [ ] tools.py created
- [ ] tool_executor.py created  
- [ ] routing.py updated
- [ ] system.txt updated
- [ ] Test: write to memory works
- [ ] Test: read from memory works
- [ ] Test: cross-session persistence works
- [ ] Groq fallback tested (no tools, honest about it)
