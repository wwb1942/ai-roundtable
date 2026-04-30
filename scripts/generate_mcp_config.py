from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_server_config(python: Path, room_file: Path) -> dict:
    return {
        "command": str(python),
        "args": [
            str(PROJECT_ROOT / "scripts" / "room_mcp_server.py"),
            "--room-file",
            str(room_file),
        ],
        "env": {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    }


def write_claude_mcp_json(path: Path, server_config: dict) -> None:
    path.write_text(
        json.dumps({"mcpServers": {"ai-roundtable-room": server_config}}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_codex_snippet(path: Path, server_config: dict) -> None:
    args = ", ".join("'" + arg.replace("\\", "\\\\").replace("'", "\\'") + "'" for arg in server_config["args"])
    content = (
        "[mcp_servers.ai-roundtable-room]\n"
        'type = "stdio"\n'
        f"command = '{server_config['command'].replace("\\", "\\\\").replace("'", "\\'")}'\n"
        f"args = [{args}]\n"
        "\n"
        "[mcp_servers.ai-roundtable-room.env]\n"
        "PYTHONUTF8 = '1'\n"
        "PYTHONIOENCODING = 'utf-8'\n"
    )
    path.write_text(content, encoding="utf-8")


def write_codex_install_script(path: Path, snippet_path: Path) -> None:
    content = f"""$ErrorActionPreference = "Stop"
$ConfigPath = Join-Path $env:USERPROFILE ".codex\\config.toml"
$SnippetPath = "{snippet_path}"
$Start = "# >>> ai-roundtable-room mcp >>>"
$End = "# <<< ai-roundtable-room mcp <<<"

if (-not (Test-Path $ConfigPath)) {{
  New-Item -ItemType File -Path $ConfigPath -Force | Out-Null
}}

$Config = Get-Content $ConfigPath -Raw
$Snippet = Get-Content $SnippetPath -Raw
$Block = "$Start`n$Snippet`n$End"
$Pattern = [regex]::Escape($Start) + "[\\s\\S]*?" + [regex]::Escape($End)

if ($Config -match $Pattern) {{
  $Config = [regex]::Replace($Config, $Pattern, [System.Text.RegularExpressions.MatchEvaluator]{{ param($m) $Block }})
}} else {{
  $Config = $Config.TrimEnd() + "`n`n" + $Block + "`n"
}}

Set-Content -Path $ConfigPath -Value $Config -Encoding UTF8
Write-Host "Installed ai-roundtable-room MCP server into $ConfigPath"
"""
    path.write_text(content, encoding="utf-8")


def install_codex_config(config_path: Path, snippet_path: Path) -> None:
    start = "# >>> ai-roundtable-room mcp >>>"
    end = "# <<< ai-roundtable-room mcp <<<"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    snippet = snippet_path.read_text(encoding="utf-8")
    block = f"{start}\n{snippet.rstrip()}\n{end}"
    pattern = re.escape(start) + r"[\s\S]*?" + re.escape(end)
    if re.search(pattern, config):
        config = re.sub(pattern, block, config)
    else:
        config = config.rstrip() + "\n\n" + block + "\n"
    config_path.write_text(config, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate MCP config files for the roundtable room.")
    parser.add_argument("--session", default="ai-roundtable")
    parser.add_argument("--room-file", default="")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "output" / "mcp"))
    parser.add_argument("--install-codex", action="store_true")
    args = parser.parse_args()

    python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    room_file = Path(args.room_file) if args.room_file else PROJECT_ROOT / "logs" / f"mcp-room-{args.session}.jsonl"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    server_config = build_server_config(python, room_file)
    write_claude_mcp_json(PROJECT_ROOT / ".mcp.json", server_config)
    codex_snippet = out_dir / "codex-ai-roundtable-room.toml"
    write_codex_snippet(codex_snippet, server_config)
    write_codex_install_script(out_dir / "install-codex-mcp.ps1", codex_snippet)
    if args.install_codex:
        install_codex_config(Path.home() / ".codex" / "config.toml", codex_snippet)

    print(f"Claude project MCP config: {PROJECT_ROOT / '.mcp.json'}")
    print(f"Codex MCP snippet: {codex_snippet}")
    print(f"Codex install script: {out_dir / 'install-codex-mcp.ps1'}")


if __name__ == "__main__":
    main()
