# on_session_end.ps1 — Claude Code SessionEnd hook for Project Pi.
# Keeps the project aligned at the end of a work session: regenerates PI.md's
# auto-sections (refresh_pi.py) once, then records the outcome to
# logs/claude_activity.jsonl. Best-effort and non-fatal — always exits 0.
# Per Ash's choice: align at session end (also runnable manually any time:
#   python scripts/refresh_pi.py).

$ErrorActionPreference = "SilentlyContinue"
try {
    $raw = [Console]::In.ReadToEnd()
    $data = if ($raw) { $raw | ConvertFrom-Json } else { $null }

    $repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $logDir = Join-Path $repo "logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $logFile = Join-Path $logDir "claude_activity.jsonl"

    # prefer the project venv python, fall back to PATH python
    $py = Join-Path $repo "pi_env\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }

    $refresh = Join-Path $repo "scripts\refresh_pi.py"
    $code = $null
    if (Test-Path $refresh) {
        Push-Location $repo
        & $py $refresh *> $null
        $code = $LASTEXITCODE
        Pop-Location
    }

    $rec = [ordered]@{
        ts           = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        event        = "session-end-align"
        session      = $data.session_id
        refresh_exit = $code
    }
    $line = ($rec | ConvertTo-Json -Compress -Depth 5)
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::AppendAllText($logFile, $line + "`n", $enc)
} catch { }

exit 0
