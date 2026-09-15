from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PARTICIPANT_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
DANGEROUS_CODEX_FLAG = "--dangerously-bypass-approvals-and-sandbox"
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
    if not resolved.drive:
        # WSL paths are already portable; this also makes the helper usable in
        # Linux CI and when callers pass a relative path.
        return resolved.as_posix()
    drive = resolved.drive.rstrip(":").lower()
    rest = resolved.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{rest}"


def build_room_command(project_root: Path, session: str, room_file: Path) -> str:
    if not SESSION_PATTERN.fullmatch(session):
        raise ValueError("session must contain only letters, numbers, '.', '_' or '-'")
    project_root_wsl = shlex.quote(windows_path_to_wsl(project_root))
    room_file_wsl = windows_path_to_wsl(room_file)
    quoted_room = shlex.quote(room_file_wsl)
    quoted_session = shlex.quote(session)
    return (
        f"cd {project_root_wsl} && "
        "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 LANG=C.UTF-8 LC_ALL=C.UTF-8 "
        f"python3 scripts/room_broker.py --session {quoted_session} --room-file {quoted_room}"
    )


def load_participant_ids(config_path: Path) -> list[str]:
    try:
        import yaml
    except ModuleNotFoundError:
        return ["claude", "codex"]
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except (OSError, TypeError, ValueError):
        return ["claude", "codex"]
    if not isinstance(loaded, dict):
        return ["claude", "codex"]
    config: dict[str, Any] = loaded
    raw_participants = config.get("participants")
    if not isinstance(raw_participants, list):
        return ["claude", "codex"]
    participant_ids = [
        str(participant["id"]).lower()
        for participant in raw_participants
        if isinstance(participant, dict) and participant.get("id")
    ]
    if not participant_ids or any(not PARTICIPANT_PATTERN.fullmatch(pid) for pid in participant_ids):
        return ["claude", "codex"]
    if len(set(participant_ids)) != len(participant_ids):
        raise ValueError("participant ids must be unique")
    return participant_ids


def load_participant_commands(config_path: Path) -> dict[str, str]:
    """Load explicit executable commands for non-built-in participants."""
    try:
        import yaml
    except ModuleNotFoundError:
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    config: dict[str, Any] = loaded
    commands: dict[str, str] = {}
    for participant in config.get("participants", []) if isinstance(config.get("participants"), list) else []:
        if not isinstance(participant, dict) or not participant.get("id"):
            continue
        command = participant.get("command")
        if isinstance(command, str) and command.strip():
            commands[str(participant["id"]).lower()] = command.strip()
    return commands


def load_moderator_name(config_path: Path) -> str:
    try:
        import yaml
    except ModuleNotFoundError:
        return "You"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except OSError:
        return "You"
    if not isinstance(loaded, dict):
        return "You"
    config: dict[str, Any] = loaded
    moderator = config.get("moderator")
    if not isinstance(moderator, dict):
        return "You"
    name = moderator.get("name")
    if not name:
        return "You"
    return str(name)


def build_participant_commands(
    project_root: Path,
    participant_ids: list[str],
    configured_commands: dict[str, str] | None = None,
    allow_unsafe_codex: bool = False,
) -> dict[str, str]:
    commands: dict[str, str] = {}
    configured = {key.lower(): value for key, value in (configured_commands or {}).items()}
    for pid in participant_ids:
        if pid in configured:
            command = configured[pid]
        elif pid == "claude":
            command = "claude"
        elif pid == "codex":
            command = "codex"
        else:
            raise ValueError(
                f"participant {pid!r} has no executable command; set participants[].command explicitly"
            )
        if pid == "codex" and DANGEROUS_CODEX_FLAG in command and not allow_unsafe_codex:
            raise ValueError("Codex safety bypass requires --allow-unsafe-codex")
        if pid == "codex" and allow_unsafe_codex and DANGEROUS_CODEX_FLAG not in command:
            command += f" {DANGEROUS_CODEX_FLAG}"
        commands[pid] = build_windows_agent_command(project_root, command)
    return commands


def powershell_single_quote(value: str) -> str:
    if any(ord(char) < 32 for char in value):
        raise ValueError("participant command/path contains control characters")
    return "'" + value.replace("'", "''") + "'"


def build_windows_agent_command(project_root: Path, command: str) -> str:
    project_root_windows = str(project_root.resolve())
    quoted_root = powershell_single_quote(project_root_windows)
    try:
        command_parts = _split_command_line(command)
    except ValueError as exc:
        raise ValueError(f"invalid participant command: {exc}") from exc
    if not command_parts:
        raise ValueError("participant command cannot be empty")
    quoted_command = " ".join(powershell_single_quote(part) for part in command_parts)
    return f'powershell.exe -NoExit -Command "Set-Location -LiteralPath {quoted_root}; & {quoted_command}"'


def _split_command_line(command: str) -> list[str]:
    """Split a configured command while preserving Windows path backslashes."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            else:
                current.append(char)
        elif char.isspace() and quote is None:
            if current:
                parts.append("".join(current))
                current = []
        elif char == "\\" and index + 1 < len(command) and command[index + 1] in {"'", '"'}:
            # An escaped quote is literal; ordinary backslashes stay intact.
            current.append(command[index + 1])
            index += 1
        else:
            current.append(char)
        index += 1
    if quote is not None:
        raise ValueError("unclosed quote")
    if current:
        parts.append("".join(current))
    return parts


def prepare_mcp_configs(
    project_root: Path,
    session: str,
    room_file: Path,
    python: Path | None = None,
    out_dir: Path | None = None,
    codex_config: Path | None = None,
    warn=print,
    participant_ids: list[str] | None = None,
) -> None:
    python = python or project_root / ".venv" / "Scripts" / "python.exe"
    out_dir = out_dir or project_root / "output" / "mcp"
    codex_config = codex_config or Path.home() / ".codex" / "config.toml"
    out_dir.mkdir(parents=True, exist_ok=True)

    bind_identities = participant_ids is None or set(participant_ids) <= {"claude", "codex"}
    claude_server_config = build_server_config(
        python,
        room_file,
        session_id=session,
        author="claude" if bind_identities else None,
    )
    codex_server_config = build_server_config(
        python,
        room_file,
        session_id=session,
        author="codex" if bind_identities else None,
    )
    write_claude_mcp_json(project_root / ".mcp.json", claude_server_config)
    codex_snippet = out_dir / "codex-ai-roundtable-room.toml"
    write_codex_snippet(codex_snippet, codex_server_config)
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
    parser.add_argument(
        "--allow-unsafe-codex",
        action="store_true",
        help="Pass Codex's approval/sandbox bypass flag (explicitly unsafe)",
    )
    parser.add_argument("--no-attach", action="store_true")
    args = parser.parse_args()

    if not SESSION_PATTERN.fullmatch(args.session):
        parser.error("--session must contain only letters, numbers, '.', '_' or '-'")

    config_path = Path(args.config)
    try:
        participant_ids = load_participant_ids(config_path)
    except ValueError as exc:
        parser.error(str(exc))
    configured_commands = load_participant_commands(config_path)
    moderator_name = load_moderator_name(config_path)
    renderer = TmuxRenderer(session_name=args.session, participants=participant_ids, moderator_name=moderator_name)
    room_file = args.room_file or str(PROJECT_ROOT / "logs" / f"mcp-room-{args.session}.jsonl")
    room_file_path = Path(room_file)
    prepare_mcp_configs(PROJECT_ROOT, args.session, room_file_path, participant_ids=participant_ids)
    room_command = build_room_command(PROJECT_ROOT, args.session, room_file_path)
    room_command = f"{room_command} --config {shlex.quote(windows_path_to_wsl(Path(args.config)))}"
    try:
        participant_commands = build_participant_commands(
            PROJECT_ROOT,
            participant_ids,
            configured_commands=configured_commands,
            allow_unsafe_codex=args.allow_unsafe_codex,
        )
        if args.claude_command and "claude" in participant_commands:
            participant_commands["claude"] = build_windows_agent_command(PROJECT_ROOT, args.claude_command)
        if args.codex_command and "codex" in participant_commands:
            if DANGEROUS_CODEX_FLAG in args.codex_command and not args.allow_unsafe_codex:
                raise ValueError("Codex safety bypass requires --allow-unsafe-codex")
            participant_commands["codex"] = build_windows_agent_command(PROJECT_ROOT, args.codex_command)
    except ValueError as exc:
        parser.error(str(exc))
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
