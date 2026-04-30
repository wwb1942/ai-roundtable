from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_mcp_config import build_server_config
from scripts.generate_mcp_config import install_codex_config
from scripts.generate_mcp_config import write_claude_mcp_json
from scripts.generate_mcp_config import write_codex_install_script
from scripts.generate_mcp_config import write_codex_snippet
from src.renderers.tmux import TmuxRenderer


def windows_path_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    rest = resolved.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{rest}"


def build_room_command(project_root: Path, session: str, room_file: Path) -> str:
    room_file_wsl = windows_path_to_wsl(room_file)
    return (
        f"cd {windows_path_to_wsl(project_root)} && "
        "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 LANG=C.UTF-8 LC_ALL=C.UTF-8 "
        f"python3 scripts/room_broker.py --session {session} --room-file {room_file_wsl}"
    )


def load_participant_ids(config_path: Path) -> list[str]:
    try:
        import yaml
    except ModuleNotFoundError:
        return ["claude", "codex"]
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config: dict[str, Any] = yaml.safe_load(f) or {}
    except OSError:
        return ["claude", "codex"]
    participant_ids = [
        str(participant["id"]).lower()
        for participant in config.get("participants", [])
        if isinstance(participant, dict) and participant.get("id")
    ]
    return participant_ids or ["claude", "codex"]


def load_moderator_name(config_path: Path) -> str:
    try:
        import yaml
    except ModuleNotFoundError:
        return "You"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config: dict[str, Any] = yaml.safe_load(f) or {}
    except OSError:
        return "You"
    moderator = config.get("moderator")
    if not isinstance(moderator, dict):
        return "You"
    name = moderator.get("name")
    if not name:
        return "You"
    return str(name)


def build_participant_commands(project_root: Path, participant_ids: list[str]) -> dict[str, str]:
    commands: dict[str, str] = {}
    for pid in participant_ids:
        if pid == "claude":
            command = "claude"
        elif pid == "codex":
            command = "codex --dangerously-bypass-approvals-and-sandbox"
        else:
            command = pid
        commands[pid] = build_windows_agent_command(project_root, command)
    return commands


def powershell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_windows_agent_command(project_root: Path, command: str) -> str:
    project_root_windows = str(project_root.resolve())
    quoted_root = powershell_single_quote(project_root_windows)
    escaped_command = command.replace('"', '`"')
    return f'powershell.exe -NoExit -Command "Set-Location -LiteralPath {quoted_root}; {escaped_command}"'


def prepare_mcp_configs(
    project_root: Path,
    session: str,
    room_file: Path,
    python: Path | None = None,
    out_dir: Path | None = None,
    codex_config: Path | None = None,
    warn=print,
) -> None:
    python = python or project_root / ".venv" / "Scripts" / "python.exe"
    out_dir = out_dir or project_root / "output" / "mcp"
    codex_config = codex_config or Path.home() / ".codex" / "config.toml"
    out_dir.mkdir(parents=True, exist_ok=True)

    server_config = build_server_config(python, room_file)
    write_claude_mcp_json(project_root / ".mcp.json", server_config)
    codex_snippet = out_dir / "codex-ai-roundtable-room.toml"
    write_codex_snippet(codex_snippet, server_config)
    codex_install_script = out_dir / "install-codex-mcp.ps1"
    write_codex_install_script(codex_install_script, codex_snippet)
    try:
        install_codex_config(codex_config, codex_snippet)
    except OSError as exc:
        warn(
            "[mcp] Could not update Codex MCP config automatically: "
            f"{exc}. Run this manually after closing other Codex sessions: "
            f"powershell -ExecutionPolicy Bypass -File {codex_install_script}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Start an interactive tmux AI roundtable.")
    parser.add_argument("--topic", default="Interactive AI roundtable")
    parser.add_argument("--session", default="ai-roundtable")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.yaml"))
    parser.add_argument("--claude-command", default="")
    parser.add_argument("--codex-command", default="")
    parser.add_argument("--room-file", default="")
    parser.add_argument("--no-attach", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config)
    participant_ids = load_participant_ids(config_path)
    moderator_name = load_moderator_name(config_path)
    renderer = TmuxRenderer(session_name=args.session, participants=participant_ids, moderator_name=moderator_name)
    room_file = args.room_file or str(PROJECT_ROOT / "logs" / f"mcp-room-{args.session}.jsonl")
    room_file_path = Path(room_file)
    prepare_mcp_configs(PROJECT_ROOT, args.session, room_file_path)
    room_command = build_room_command(PROJECT_ROOT, args.session, room_file_path)
    room_command = f"{room_command} --config {windows_path_to_wsl(Path(args.config))}"
    participant_commands = build_participant_commands(PROJECT_ROOT, participant_ids)
    if args.claude_command and "claude" in participant_commands:
        participant_commands["claude"] = args.claude_command
    if args.codex_command and "codex" in participant_commands:
        participant_commands["codex"] = args.codex_command
    renderer.start_interactive(
        participant_commands=participant_commands,
        topic=args.topic,
        room_command=room_command,
        working_directory=windows_path_to_wsl(PROJECT_ROOT),
    )
    print(f"tmux session created: {args.session}")
    print("attach command:", " ".join(renderer.attach_command()))
    if not args.no_attach:
        renderer.attach()


if __name__ == "__main__":
    main()
