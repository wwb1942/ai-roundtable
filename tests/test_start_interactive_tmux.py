import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import start_interactive_tmux
from scripts.start_interactive_tmux import build_windows_agent_command
from scripts.start_interactive_tmux import build_participant_commands, _split_command_line
from scripts.start_interactive_tmux import windows_path_to_wsl


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific path conversion")


@windows_only
def test_windows_path_to_wsl_path():
    assert windows_path_to_wsl(Path("D:/projects/ai-roundtable-codex")) == "/mnt/d/projects/ai-roundtable-codex"


@windows_only
def test_build_room_command_forces_utf8_environment():
    command = start_interactive_tmux.build_room_command(
        project_root=Path("D:/projects/ai-roundtable-codex"),
        session="ai-roundtable",
        room_file=Path("D:/projects/ai-roundtable-codex/logs/room.jsonl"),
    )

    assert "PYTHONUTF8=1" in command
    assert "PYTHONIOENCODING=utf-8" in command
    assert "LANG=C.UTF-8" in command
    assert "LC_ALL=C.UTF-8" in command


@windows_only
def test_build_windows_agent_command_runs_from_project_directory():
    command = build_windows_agent_command(Path("D:/projects/ai-roundtable-codex"), "codex")

    assert command.startswith("powershell.exe -NoExit")
    assert "Set-Location -LiteralPath 'D:\\projects\\ai-roundtable-codex'" in command
    assert command.endswith("; & 'codex'\"")


@windows_only
def test_build_windows_agent_command_quotes_shell_metacharacters():
    command = build_windows_agent_command(Path("D:/projects/ai-roundtable-codex"), "codex; Write-Output PWN")

    assert "; Write-Output" not in command.replace("'codex;' 'Write-Output'", "")
    assert "& 'codex;' 'Write-Output' 'PWN'" in command


def test_unknown_participant_requires_explicit_command():
    with pytest.raises(ValueError, match="explicitly"):
        build_participant_commands(Path("."), ["gemini"])


def test_codex_safety_bypass_is_opt_in():
    safe = build_participant_commands(Path("."), ["codex"])["codex"]
    unsafe = build_participant_commands(Path("."), ["codex"], allow_unsafe_codex=True)["codex"]

    assert "dangerously-bypass" not in safe
    assert "dangerously-bypass" in unsafe


@windows_only
def test_custom_cli_override_is_wrapped_as_literal_command():
    command = build_windows_agent_command(Path("D:/projects/ai-roundtable-codex"), "codex; Write-Output PWN")

    assert "& 'codex;' 'Write-Output' 'PWN'" in command


def test_command_line_parser_preserves_quoted_arguments_and_windows_paths():
    assert _split_command_line('codex --arg="a b" --path="C:\\Program Files\\tool"') == [
        "codex",
        "--arg=a b",
        "--path=C:\\Program Files\\tool",
    ]


def test_load_moderator_name_from_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
moderator:
  name: Alice
participants:
  - id: claude
""".lstrip(),
        encoding="utf-8",
    )

    with patch.dict(
        "sys.modules",
        {"yaml": SimpleNamespace(safe_load=lambda stream: {"moderator": {"name": "Alice"}})},
    ):
        assert start_interactive_tmux.load_moderator_name(config) == "Alice"


def test_load_moderator_name_defaults_when_block_missing(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
participants:
  - id: claude
""".lstrip(),
        encoding="utf-8",
    )

    assert start_interactive_tmux.load_moderator_name(config) == "You"


def test_load_moderator_name_defaults_when_config_missing(tmp_path):
    assert start_interactive_tmux.load_moderator_name(tmp_path / "missing.yaml") == "You"


def test_prepare_mcp_configs_installs_codex_config(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    codex_config = tmp_path / "codex" / "config.toml"

    start_interactive_tmux.prepare_mcp_configs(
        project_root=project_root,
        session="ai-roundtable",
        room_file=project_root / "logs" / "room.jsonl",
        python=project_root / ".venv" / "Scripts" / "python.exe",
        out_dir=project_root / "output" / "mcp",
        codex_config=codex_config,
    )

    assert (project_root / ".mcp.json").exists()
    assert "ai-roundtable-room" in codex_config.read_text(encoding="utf-8")


def test_prepare_mcp_configs_does_not_fail_when_codex_config_is_locked(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    codex_config = tmp_path / "codex" / "config.toml"
    warnings = []

    def locked_install(_config_path, _snippet_path):
        raise PermissionError("locked")

    with patch.object(start_interactive_tmux, "install_codex_config", side_effect=locked_install):
        start_interactive_tmux.prepare_mcp_configs(
            project_root=project_root,
            session="ai-roundtable",
            room_file=project_root / "logs" / "room.jsonl",
            python=project_root / ".venv" / "Scripts" / "python.exe",
            out_dir=project_root / "output" / "mcp",
            codex_config=codex_config,
            warn=warnings.append,
        )

    assert (project_root / ".mcp.json").exists()
    assert (project_root / "output" / "mcp" / "codex-ai-roundtable-room.toml").exists()
    assert any("Could not update Codex MCP config" in warning for warning in warnings)


def test_prepare_mcp_configs_does_not_bind_single_author_for_alias_participants(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    start_interactive_tmux.prepare_mcp_configs(
        project_root=project_root,
        session="ai-roundtable",
        room_file=project_root / "logs" / "room.jsonl",
        python=project_root / ".venv" / "Scripts" / "python.exe",
        out_dir=project_root / "output" / "mcp",
        codex_config=tmp_path / "codex.toml",
        participant_ids=["claude", "codex", "gemini"],
    )

    assert "--author" not in (project_root / ".mcp.json").read_text(encoding="utf-8")


@windows_only
def test_main_passes_project_directory_to_tmux_renderer(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
participants:
  - id: claude
    plugin: claude
  - id: codex
    plugin: codex
moderator:
  name: You
""".lstrip(),
        encoding="utf-8",
    )

    class FakeRenderer:
        def __init__(self, session_name, participants, moderator_name="You"):
            self.session_name = session_name
            self.participants = participants
            self.moderator_name = moderator_name
            self.start_kwargs = None

        def start_interactive(self, **kwargs):
            self.start_kwargs = kwargs

        def attach_command(self):
            return ["tmux", "attach-session", "-t", self.session_name]

        def attach(self):
            raise AssertionError("attach should not be called with --no-attach")

    renderer = FakeRenderer("ai-roundtable", [])

    with (
        patch.object(start_interactive_tmux, "TmuxRenderer", return_value=renderer) as renderer_cls,
        patch.object(start_interactive_tmux, "prepare_mcp_configs"),
        patch.object(
            start_interactive_tmux,
            "PROJECT_ROOT",
            Path("D:/projects/ai-roundtable-codex"),
        ),
        patch("sys.argv", ["start_interactive_tmux.py", "--no-attach", "--config", str(config)]),
    ):
        start_interactive_tmux.main()

    assert renderer_cls.call_args.kwargs["participants"] == ["claude", "codex"]
    assert renderer_cls.call_args.kwargs["moderator_name"] == "You"
    assert renderer.start_kwargs["working_directory"] == "/mnt/d/projects/ai-roundtable-codex"
    commands = renderer.start_kwargs["participant_commands"]
    assert "Set-Location -LiteralPath 'D:\\projects\\ai-roundtable-codex'" in commands["claude"]
    assert "Set-Location -LiteralPath 'D:\\projects\\ai-roundtable-codex'" in commands["codex"]
    assert commands["claude"].endswith("; & 'claude'\"")
    assert commands["codex"].endswith("; & 'codex'\"")


@windows_only
def test_main_loads_participants_and_builds_unknown_agent_commands(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
participants:
  - id: claude
    plugin: claude
  - id: codex
    plugin: codex
  - id: gemini
    plugin: claude
""".lstrip(),
        encoding="utf-8",
    )

    class FakeRenderer:
        def __init__(self, session_name, participants, moderator_name="You"):
            self.session_name = session_name
            self.participants = participants
            self.moderator_name = moderator_name
            self.start_kwargs = None

        def start_interactive(self, **kwargs):
            self.start_kwargs = kwargs

        def attach_command(self):
            return ["tmux", "attach-session", "-t", self.session_name]

        def attach(self):
            raise AssertionError("attach should not be called with --no-attach")

    renderer = FakeRenderer("ai-roundtable", [])

    with (
        patch.dict(
            "sys.modules",
            {
                "yaml": SimpleNamespace(
                    safe_load=lambda stream: {
                        "participants": [
                            {"id": "claude", "plugin": "claude"},
                            {"id": "codex", "plugin": "codex"},
                            {"id": "gemini", "plugin": "claude", "command": "gemini"},
                        ]
                    }
                )
            },
        ),
        patch.object(start_interactive_tmux, "TmuxRenderer", return_value=renderer) as renderer_cls,
        patch.object(start_interactive_tmux, "prepare_mcp_configs"),
        patch.object(
            start_interactive_tmux,
            "PROJECT_ROOT",
            Path("D:/projects/ai-roundtable-codex"),
        ),
        patch("sys.argv", ["start_interactive_tmux.py", "--no-attach", "--config", str(config)]),
    ):
        start_interactive_tmux.main()

    assert renderer_cls.call_args.kwargs["participants"] == ["claude", "codex", "gemini"]
    assert set(renderer.start_kwargs["participant_commands"]) == {"claude", "codex", "gemini"}
    assert renderer.start_kwargs["participant_commands"]["gemini"].endswith("; & 'gemini'\"")


@windows_only
def test_main_passes_configured_moderator_name_to_tmux_renderer(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
moderator:
  name: Alice
participants:
  - id: claude
    plugin: claude
  - id: codex
    plugin: codex
""".lstrip(),
        encoding="utf-8",
    )

    class FakeRenderer:
        def __init__(self, session_name, participants, moderator_name="You"):
            self.session_name = session_name
            self.participants = participants
            self.moderator_name = moderator_name
            self.start_kwargs = None

        def start_interactive(self, **kwargs):
            self.start_kwargs = kwargs

        def attach_command(self):
            return ["tmux", "attach-session", "-t", self.session_name]

        def attach(self):
            raise AssertionError("attach should not be called with --no-attach")

    renderer = FakeRenderer("ai-roundtable", [])

    with (
        patch.dict(
            "sys.modules",
            {
                "yaml": SimpleNamespace(
                    safe_load=lambda stream: {
                        "moderator": {"name": "Alice"},
                        "participants": [
                            {"id": "claude", "plugin": "claude"},
                            {"id": "codex", "plugin": "codex"},
                        ],
                    }
                )
            },
        ),
        patch.object(start_interactive_tmux, "TmuxRenderer", return_value=renderer) as renderer_cls,
        patch.object(start_interactive_tmux, "prepare_mcp_configs"),
        patch.object(
            start_interactive_tmux,
            "PROJECT_ROOT",
            Path("D:/projects/ai-roundtable-codex"),
        ),
        patch("sys.argv", ["start_interactive_tmux.py", "--no-attach", "--config", str(config)]),
    ):
        start_interactive_tmux.main()

    assert renderer_cls.call_args.kwargs["moderator_name"] == "Alice"
