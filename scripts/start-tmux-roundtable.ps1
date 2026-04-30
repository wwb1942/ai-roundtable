param(
  [string]$Topic = "Interactive AI roundtable",
  [string]$Session = "ai-roundtable",
  [string]$ClaudeCommand = "",
  [string]$CodexCommand = "",
  [string]$RoomFile = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 | Out-Null

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  Write-Host "[setup] Creating local virtualenv..."
  python -m venv (Join-Path $ProjectRoot ".venv")
}

Write-Host "[setup] Installing Python dependencies..."
& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")

Write-Host "[setup] Checking WSL..."
& wsl.exe sh -lc 'echo wsl-ok' | Out-Host

Write-Host "[setup] Checking tmux in WSL..."
$tmuxCheck = & wsl.exe sh -lc 'command -v tmux >/dev/null 2>&1; echo $?'
if ($tmuxCheck.Trim() -ne "0") {
  Write-Host "[setup] tmux not found. Installing with apt..."
  & wsl.exe sh -lc 'sudo apt update; sudo apt install -y tmux'
}

Write-Host "[start] Creating interactive tmux room..."
Set-Location -LiteralPath $ProjectRoot
if (-not $RoomFile) {
  $RoomFile = Join-Path $ProjectRoot "logs\mcp-room-$Session.jsonl"
}

Write-Host "[mcp] Generating MCP config files..."
& $Python scripts\generate_mcp_config.py --session $Session --room-file $RoomFile | Out-Host
$CodexMcpInstall = Join-Path $ProjectRoot "output\mcp\install-codex-mcp.ps1"
Write-Host "[mcp] Installing Codex MCP config..."
try {
  & powershell -ExecutionPolicy Bypass -File $CodexMcpInstall | Out-Host
} catch {
  Write-Warning "[mcp] Codex MCP config install failed: $($_.Exception.Message)"
  Write-Warning "[mcp] Close other Codex sessions and run manually if Codex cannot see room tools:"
  Write-Warning "      powershell -ExecutionPolicy Bypass -File $CodexMcpInstall"
}

$pyArgs = @(
  "scripts\start_interactive_tmux.py",
  "--topic", $Topic,
  "--session", $Session,
  "--room-file", $RoomFile,
  "--no-attach"
)
if ($ClaudeCommand) { $pyArgs += @("--claude-command", $ClaudeCommand) }
if ($CodexCommand) { $pyArgs += @("--codex-command", $CodexCommand) }
& $Python @pyArgs

$McpCommand = "$Python scripts\room_mcp_server.py --room-file `"$RoomFile`""
Write-Host "[mcp] Room MCP server command:"
Write-Host "      $McpCommand"
Write-Host "[mcp] Claude should load project config: $ProjectRoot\.mcp.json"
Write-Host "[mcp] Codex MCP config installed from: $CodexMcpInstall"

Write-Host "[attach] Attaching to tmux session '$Session'. Detach with Ctrl+B then D."
& wsl.exe tmux attach-session -t $Session
