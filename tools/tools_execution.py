"""
Pi Agent Tools - Execution
Tools for running code and self-modification.
"""

import difflib
import subprocess
import os
from typing import Dict, Any, Optional


class ExecutionTools:
    """
    Simple execution tools.
    Run Python, run bash, modify files.
    """

    def __init__(self, project_root: str = None):
        if project_root is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.project_root = project_root
        # T-182: per-session read ledger {abs_path → mtime_at_read}
        self._read_ledger: Dict[str, float] = {}
    
    def execute_python(self, code: str, return_output: bool = True) -> Dict:
        """
        Execute Python code.
        
        Args:
            code: Python code to run
            return_output: Capture stdout/stderr
        
        Returns:
            {"success": bool, "output": str, "error": str}
        """
        
        try:
            # Create temp file
            temp_file = os.path.join(self.project_root, "temp_exec.py")
            
            with open(temp_file, 'w') as f:
                f.write(code)
            
            # Execute
            result = subprocess.run(
                ["python", temp_file],
                capture_output=return_output,
                text=True,
                timeout=30
            )
            
            # Clean up
            if os.path.exists(temp_file):
                os.remove(temp_file)
            
            return {
                "success": result.returncode == 0,
                "output": result.stdout if return_output else "",
                "error": result.stderr if return_output else "",
                "code": result.returncode
            }
            
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": "Execution timed out (30s limit)",
                "code": -1
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e),
                "code": -1
            }
    
    def execute_bash(self, command: str, cwd: Optional[str] = None) -> Dict:
        """
        Execute bash command.
        
        Args:
            command: Bash command to run
            cwd: Working directory (default: project root)
        
        Returns:
            {"success": bool, "output": str, "error": str}
        """
        
        if cwd is None:
            cwd = self.project_root
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=30
            )
            
            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr,
                "code": result.returncode
            }
            
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": "Command timed out (30s limit)",
                "code": -1
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e),
                "code": -1
            }
    
    def modify_file(self, path: str, old_str: str, new_str: str) -> Dict:
        """
        Modify file (including self-modification).

        Args:
            path: File path (relative to project root or absolute)
            old_str: String to replace (must be unique)
            new_str: Replacement string

        Returns:
            {"success": bool, "message": str, "diff": str, "lines_changed": int}

        T-182: read-before-write safety.
        - Never-read files are refused with a hint to call read_file first.
        - Files whose mtime changed since read return a stale_read error.
        - Every successful edit includes a unified diff + line count.
        """
        # Make absolute
        if not os.path.isabs(path):
            path = os.path.join(self.project_root, path)
        abs_path = path

        if not os.path.exists(abs_path):
            return {"success": False, "message": f"File not found: {abs_path}"}

        # T-182 preflight: read-before-write guard
        current_mtime = os.path.getmtime(abs_path)
        if abs_path not in self._read_ledger:
            return {
                "success": False,
                "error": "read_before_write",
                "message": (
                    f"File '{abs_path}' has not been read this session. "
                    "Call read_file first to see the current content before modifying."
                ),
            }
        recorded_mtime = self._read_ledger[abs_path]
        if abs(current_mtime - recorded_mtime) > 0.01:
            return {
                "success": False,
                "error": "stale_read",
                "message": (
                    f"File '{abs_path}' changed after you read it (mtime drift: "
                    f"{current_mtime - recorded_mtime:+.2f}s). Re-read it first."
                ),
            }

        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if old_str not in content:
                return {
                    "success": False,
                    "message": f"String not found in file: '{old_str[:50]}...'"
                }

            if content.count(old_str) > 1:
                return {
                    "success": False,
                    "message": f"String appears {content.count(old_str)} times (must be unique)"
                }

            new_content = content.replace(old_str, new_str)

            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            # T-182: generate unified diff (capped at 200 lines)
            diff_lines = list(difflib.unified_diff(
                content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{os.path.basename(abs_path)}",
                tofile=f"b/{os.path.basename(abs_path)}",
                n=3,
            ))
            diff_str = "".join(diff_lines[:200])
            lines_changed = sum(1 for l in diff_lines if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))

            # Update ledger mtime after successful write
            self._read_ledger[abs_path] = os.path.getmtime(abs_path)

            with open(abs_path, 'r', encoding='utf-8') as f:
                verify_content = f.read()

            if new_str in verify_content:
                return {
                    "success": True,
                    "message": f"Modified {abs_path}",
                    "verified": True,
                    "diff": diff_str,
                    "lines_changed": lines_changed,
                }
            else:
                return {
                    "success": False,
                    "message": "Modification failed verification",
                    "verified": False,
                }

        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def read_file(self, path: str, lines: Optional[tuple] = None) -> Dict:
        """
        Read file contents.
        
        Args:
            path: File path
            lines: Optional (start, end) line numbers
        
        Returns:
            {"success": bool, "content": str}
        """
        
        # Make absolute
        if not os.path.isabs(path):
            path = os.path.join(self.project_root, path)
        
        if not os.path.exists(path):
            return {
                "success": False,
                "content": "",
                "error": f"File not found: {path}"
            }
        
        try:
            mtime = os.path.getmtime(path)
            with open(path, 'r', encoding='utf-8') as f:
                if lines:
                    all_lines = f.readlines()
                    start, end = lines
                    content = ''.join(all_lines[start-1:end])
                else:
                    content = f.read()

            # T-182: record read in the ledger so modify_file can guard against stale reads
            self._read_ledger[path] = mtime

            return {
                "success": True,
                "content": content,
                "path": path
            }

        except Exception as e:
            return {
                "success": False,
                "content": "",
                "error": str(e)
            }
    
    def create_file(self, path: str, content: str) -> Dict:
        """
        Create new file.
        
        Args:
            path: File path
            content: File content
        
        Returns:
            {"success": bool, "message": str}
        """
        
        # Make absolute
        if not os.path.isabs(path):
            path = os.path.join(self.project_root, path)
        
        # T-182: refuse to overwrite existing files — use modify_file instead
        if os.path.exists(path):
            return {
                "success": False,
                "error": "file_exists",
                "message": (
                    f"'{path}' already exists. Use modify_file to edit it, "
                    "or choose a different path for a new file."
                ),
            }

        # Create directory if needed
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Verify
            if os.path.exists(path):
                return {
                    "success": True,
                    "message": f"Created {path}",
                    "path": path
                }
            else:
                return {
                    "success": False,
                    "message": "File creation failed verification"
                }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error: {str(e)}"
            }
    
    def list_files(self, directory: str = ".", pattern: Optional[str] = None) -> Dict:
        """
        List files in directory.
        
        Args:
            directory: Directory to list
            pattern: Optional glob pattern (e.g., "*.py")
        
        Returns:
            {"success": bool, "files": list}
        """
        
        # Make absolute
        if not os.path.isabs(directory):
            directory = os.path.join(self.project_root, directory)
        
        try:
            if pattern:
                import glob
                files = glob.glob(os.path.join(directory, pattern))
            else:
                files = [
                    os.path.join(directory, f)
                    for f in os.listdir(directory)
                    if os.path.isfile(os.path.join(directory, f))
                ]
            
            return {
                "success": True,
                "files": files,
                "count": len(files)
            }
            
        except Exception as e:
            return {
                "success": False,
                "files": [],
                "error": str(e)
            }


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_execute_python(agent, tool_input, *, memory_override=None):
    return agent.execution.execute_python(code=tool_input["code"])


def _handle_execute_bash(agent, tool_input, *, memory_override=None):
    return agent.execution.execute_bash(command=tool_input["command"])


def _handle_read_file(agent, tool_input, *, memory_override=None):
    return agent.execution.read_file(path=tool_input["path"])


def _handle_modify_file(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    result = agent.execution.modify_file(
        path=tool_input["path"],
        old_str=tool_input["old_str"],
        new_str=tool_input["new_str"],
    )
    if result.get("success"):
        mem.memory_write(
            content=f"Modified file: {tool_input['path']}",
            tier="l3", importance=3, category="file_operations",
            session_id=agent.session_id,
        )
    return result


def _handle_create_file(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    result = agent.execution.create_file(
        path=tool_input["path"],
        content=tool_input["content"],
    )
    if result.get("success"):
        mem.memory_write(
            content=f"Created file: {tool_input['path']}",
            tier="l3", importance=3, category="file_operations",
            session_id=agent.session_id,
        )
    return result


TOOLS = [
    ToolSpec(
        name="execute_python",
        description="Execute Python code. Returns output/errors.",
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        handler=_handle_execute_python,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="execute_bash",
        description="Execute bash command.",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        handler=_handle_execute_bash,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="read_file",
        description="Read file contents.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=_handle_read_file,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="modify_file",
        description="Modify file (including self). String must be unique.",
        input_schema={
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
            },
            "required": ["path", "old_str", "new_str"],
        },
        handler=_handle_modify_file,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="create_file",
        description="Create a new file with given content.",
        input_schema={
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler=_handle_create_file,
        success_predicate=lambda r: r.get("success", False),
    ),
]


if __name__ == "__main__":
    # Test
    exec_tools = ExecutionTools()
    
    # Test Python execution
    result = exec_tools.execute_python("""
print("Testing Pi agent execution")
x = 2 + 2
print(f"2 + 2 = {x}")
""")
    print(f"Python execution: {result}")
    
    # Test file modification (on self)
    result = exec_tools.read_file("tools_execution.py", lines=(1, 10))
    print(f"Read file: {result['success']}")