"""
Pi Agent Tools - Execution
Tools for running code and self-modification.
"""

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
            {"success": bool, "message": str}
        """
        
        # Make absolute
        if not os.path.isabs(path):
            path = os.path.join(self.project_root, path)
        
        if not os.path.exists(path):
            return {
                "success": False,
                "message": f"File not found: {path}"
            }
        
        try:
            # Read file
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check if old_str exists
            if old_str not in content:
                return {
                    "success": False,
                    "message": f"String not found in file: '{old_str[:50]}...'"
                }
            
            # Check if unique
            if content.count(old_str) > 1:
                return {
                    "success": False,
                    "message": f"String appears {content.count(old_str)} times (must be unique)"
                }
            
            # Replace
            new_content = content.replace(old_str, new_str)
            
            # Write back
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Verify
            with open(path, 'r', encoding='utf-8') as f:
                verify_content = f.read()
            
            if new_str in verify_content:
                return {
                    "success": True,
                    "message": f"Modified {path}",
                    "verified": True
                }
            else:
                return {
                    "success": False,
                    "message": "Modification failed verification",
                    "verified": False
                }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error: {str(e)}"
            }
    
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
            with open(path, 'r', encoding='utf-8') as f:
                if lines:
                    all_lines = f.readlines()
                    start, end = lines
                    content = ''.join(all_lines[start-1:end])
                else:
                    content = f.read()
            
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
        
        # Create directory if needed
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
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