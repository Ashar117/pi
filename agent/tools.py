"""Tool schemas and dispatch — what Claude is allowed to call and how to execute it.

Mechanical lift from PiAgent._get_tool_definitions and PiAgent._execute_tool
(Phase 4) — no behaviour change. Tool definitions are pure data; dispatch
takes the PiAgent instance to access memory/execution/evolution subsystems.
"""
from datetime import datetime, timezone
from typing import Dict, List, Any


def get_tool_definitions() -> List[Dict]:
    """Return the static list of tool schemas Claude sees in root mode."""
    return [
        {
            "name": "memory_read",
            "description": "Search memory. Returns matching entries.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                    "tier": {"type": "string", "enum": ["l1", "l2", "l3"], "description": "Optional tier filter"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "memory_write",
            "description": "Write to memory. Auto-verifies.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "tier": {"type": "string", "enum": ["l1", "l2", "l3"], "default": "l3"},
                    "importance": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    "category": {"type": "string", "default": "note"},
                    "expiry": {"type": "string", "description": "ISO datetime"}
                },
                "required": ["content"]
            }
        },
        {
            "name": "memory_delete",
            "description": "Delete from memory. Soft delete = archive to L2.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "soft": {"type": "boolean", "default": True}
                },
                "required": ["target"]
            }
        },
        {
            "name": "execute_python",
            "description": "Execute Python code. Returns output/errors.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"}
                },
                "required": ["code"]
            }
        },
        {
            "name": "execute_bash",
            "description": "Execute bash command.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"}
                },
                "required": ["command"]
            }
        },
        {
            "name": "read_file",
            "description": "Read file contents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "modify_file",
            "description": "Modify file (including self). String must be unique.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"}
                },
                "required": ["path", "old_str", "new_str"]
            }
        },
        {
            "name": "create_file",
            "description": "Create a new file with given content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        }
    ]


def execute_tool(agent, tool_name: str, tool_input: Dict) -> Any:
    """Execute a tool by name and track per-tool pattern stats.

    Operates on the PiAgent instance to access memory/execution/evolution
    subsystems. Mechanical lift from PiAgent._execute_tool — same dispatch
    table, same success-flag logic, same evolution.track_pattern call.
    """
    start_time = datetime.now(timezone.utc)
    success = False

    try:
        if tool_name == "memory_read":
            result = agent.memory.memory_read(
                query=tool_input["query"],
                tier=tool_input.get("tier")
            )
            success = True

        elif tool_name == "memory_write":
            expiry = None
            if "expiry" in tool_input and tool_input["expiry"]:
                expiry = datetime.fromisoformat(tool_input["expiry"])

            result = agent.memory.memory_write(
                content=tool_input["content"],
                tier=tool_input.get("tier", "l3"),
                importance=tool_input.get("importance", 5),
                category=tool_input.get("category", "note"),
                expiry=expiry,
                session_id=agent.session_id  # T-013: consistent L1 threading
            )
            success = result.get("verified", False)

        elif tool_name == "memory_delete":
            result = agent.memory.memory_delete(
                target=tool_input["target"],
                soft=tool_input.get("soft", True)
            )
            success = result.get("deleted", 0) > 0

        elif tool_name == "execute_python":
            result = agent.execution.execute_python(code=tool_input["code"])
            success = result.get("success", False)

        elif tool_name == "execute_bash":
            result = agent.execution.execute_bash(command=tool_input["command"])
            success = result.get("success", False)

        elif tool_name == "read_file":
            result = agent.execution.read_file(path=tool_input["path"])
            success = result.get("success", False)

        elif tool_name == "modify_file":
            result = agent.execution.modify_file(
                path=tool_input["path"],
                old_str=tool_input["old_str"],
                new_str=tool_input["new_str"]
            )
            success = result.get("success", False)
            if success:
                agent.memory.memory_write(
                    content=f"Modified file: {tool_input['path']}",
                    tier="l3", importance=3, category="file_operations",
                    session_id=agent.session_id
                )

        elif tool_name == "create_file":
            result = agent.execution.create_file(
                path=tool_input["path"],
                content=tool_input["content"]
            )
            success = result.get("success", False)
            if success:
                agent.memory.memory_write(
                    content=f"Created file: {tool_input['path']}",
                    tier="l3", importance=3, category="file_operations",
                    session_id=agent.session_id
                )

        else:
            result = {"error": f"Unknown tool: {tool_name}"}
            success = False

        # Track pattern
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        agent.evolution.track_pattern(
            pattern_name=f"tool_{tool_name}",
            success=success,
            metadata={"duration_seconds": duration}
        )

        return result

    except Exception as e:
        agent.evolution.track_pattern(
            pattern_name=f"tool_{tool_name}",
            success=False,
            metadata={"error": str(e)}
        )
        return {"error": str(e), "success": False}
