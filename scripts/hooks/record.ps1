# record.ps1 — Claude Code hook recorder for Project Pi.
# Reads the hook's JSON payload from stdin and appends one structured line to
# logs/claude_activity.jsonl (gitignored). Used by PostToolUse + PostToolUseFailure
# hooks so every file change, command, and tool error is captured automatically.
# Never fails the hook: all errors are swallowed and it exits 0.
param([string]$Event = "activity")

$ErrorActionPreference = "SilentlyContinue"
try {
    $raw = [Console]::In.ReadToEnd()
    $data = if ($raw) { $raw | ConvertFrom-Json } else { $null }

    # scripts/hooks -> scripts -> repo root
    $repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $logDir = Join-Path $repo "logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $logFile = Join-Path $logDir "claude_activity.jsonl"

    $rec = [ordered]@{
        ts      = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        event   = $Event
        tool    = $data.tool_name
        session = $data.session_id
    }

    $fp = $data.tool_input.file_path
    if ($fp) { $rec.file = [string]$fp }

    $cmd = $data.tool_input.command
    if ($cmd) {
        $cmd = [string]$cmd
        if ($cmd.Length -gt 300) { $cmd = $cmd.Substring(0, 300) }
        $rec.command = $cmd
    }

    if ($null -ne $data.tool_response.success) { $rec.success = [bool]$data.tool_response.success }

    $line = ($rec | ConvertTo-Json -Compress -Depth 5)
    $enc = New-Object System.Text.UTF8Encoding($false)   # no BOM, safe for append
    [System.IO.File]::AppendAllText($logFile, $line + "`n", $enc)
} catch { }

exit 0
