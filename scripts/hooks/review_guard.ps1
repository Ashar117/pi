# review_guard.ps1 - thin wrapper so the PostToolUse hook can run the deterministic
# review guard with the project venv python. Reads the hook JSON from stdin and pipes
# it to review_guard.py, whose stdout (a hookSpecificOutput JSON when there are
# HIGH-severity findings) is passed straight back to Claude Code. Non-blocking.
$ErrorActionPreference = 'SilentlyContinue'
try {
    $repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $py = Join-Path $repo 'pi_env\Scripts\python.exe'
    if (-not (Test-Path $py)) { $py = 'python' }
    $raw = [Console]::In.ReadToEnd()
    $raw | & $py (Join-Path $repo 'scripts\hooks\review_guard.py')
} catch { }
exit 0
