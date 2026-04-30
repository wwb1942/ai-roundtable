import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import main as main_module
from main import _build_plugin, _handle_waiting, _handle_waiting_for_degraded, _make_progress_handler
from src.models import SessionState
from src.report import generate_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_help_does_not_require_runtime_config_dependencies():
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "main.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp,
        )

    assert result.returncode == 0
    assert "AI Roundtable Discussion" in result.stdout


def test_build_plugin_passes_command_subcommand_and_args():
    class FakePlugin:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    plugin = _build_plugin(
        FakePlugin,
        {"command": "tool", "subcommand": "run", "args": ["--flag"], "session_mode": "context-managed"},
        {"call_timeout": 99},
    )

    assert plugin.kwargs == {
        "timeout": 99,
        "command": "tool",
        "subcommand": "run",
        "args": ["--flag"],
    }


def test_progress_handler_writes_to_tmux_renderer():
    class FakeTmuxRenderer:
        def __init__(self):
            self.room = []
            self.participants = []

        def write_room(self, text):
            self.room.append(text)

        def write_participant(self, participant_id, text):
            self.participants.append((participant_id, text))

    renderer = FakeTmuxRenderer()
    handler = _make_progress_handler(renderer)
    handler({"type": "turn_prompted", "data": {"plugin_id": "claude", "round": 1}})
    handler({"type": "turn_failed", "data": {"plugin_id": "claude", "error": {"message": "boom", "detail": "detail"}}})

    assert any("Calling claude" in item for item in renderer.room)
    assert ("claude", "[Round 1] Calling claude...") in renderer.participants
    assert any(item[0] == "claude" and "boom" in item[1] for item in renderer.participants)


def test_main_passes_config_participant_ids_to_tmux_renderer(tmp_path):
    created = {}

    class FakePlugin:
        display_name = "Fake"
        id = "fake"

        def __init__(self, **kwargs):
            pass

    class FakeTerminalRenderer:
        def render_header(self, *args):
            pass

        def render_report(self, *args):
            pass

    class FakeTmuxRenderer:
        def __init__(self, **kwargs):
            created["tmux_kwargs"] = kwargs

        def start(self, topic, participants):
            created["tmux_start"] = (topic, participants)

        def attach_command(self):
            return ["tmux", "attach-session", "-t", "test-room"]

        def write_room(self, text):
            pass

        def attach(self):
            raise AssertionError("attach should not be called with --no-attach")

    class FakeOrchestrator:
        state = SessionState.COMPLETED
        end_reason = "max_rounds"
        turns = []
        participant_names = ["Fake", "Fake", "Fake"]
        topic = "topic"

        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def run_auto(self):
            pass

    config = {
        "participants": [
            {"id": "claude", "plugin": "fake"},
            {"id": "codex", "plugin": "fake"},
            {"id": "gemini", "plugin": "fake"},
        ],
        "settings": {},
    }

    with (
        patch.object(main_module, "load_config", return_value=config),
        patch.object(main_module, "PLUGIN_REGISTRY", {"fake": FakePlugin}),
        patch.object(main_module, "TerminalRenderer", FakeTerminalRenderer),
        patch.object(main_module, "TmuxRenderer", FakeTmuxRenderer),
        patch.object(main_module, "Orchestrator", FakeOrchestrator),
        patch.object(main_module, "generate_report", return_value=("markdown", "{}")),
        patch(
            "sys.argv",
            [
                "main.py",
                "topic",
                "--renderer",
                "tmux",
                "--tmux-session",
                "test-room",
                "--output-dir",
                str(tmp_path),
                "--no-attach",
            ],
        ),
    ):
        main_module.main()

    assert created["tmux_kwargs"]["participants"] == ["claude", "codex", "gemini"]


class FakeRenderer:
    def __init__(self):
        self.statuses = []

    def render_status(self, *args):
        self.statuses.append(args)


class FakeDegradedOrch:
    state = SessionState.WAITING_FOR_USER
    end_reason = "degraded"
    round_count = 1

    def __init__(self):
        self.finalize_mode = None

    def finalize_degraded(self, mode="end"):
        self.finalize_mode = mode
        self.state = SessionState.COMPLETED


class FakeStalemateOrch:
    state = SessionState.WAITING_FOR_USER
    end_reason = None
    round_count = 1

    def __init__(self):
        self.finalized_by_user = False

    def finalize_user_ended(self):
        self.finalized_by_user = True
        self.end_reason = "user_ended"
        self.state = SessionState.COMPLETED

    def inject_user_input(self, text):
        raise AssertionError("end should not inject user input")

    def resume_after_user(self):
        raise AssertionError("end should not resume")

    def run_auto(self):
        raise AssertionError("end should not continue auto run")


def test_stalemate_waiting_end_finalizes_with_user_ended_reason():
    orch = FakeStalemateOrch()
    with patch("builtins.input", return_value="end"):
        result = _handle_waiting(orch, FakeRenderer())

    assert result == "finalized"
    assert orch.finalized_by_user is True
    assert orch.end_reason == "user_ended"


def test_stalemate_waiting_end_report_uses_user_ended_reason():
    orch = FakeStalemateOrch()
    with patch("builtins.input", return_value="end"):
        _handle_waiting(orch, FakeRenderer())

    md_report, _ = generate_report(
        topic="test topic",
        participants=["Claude", "Codex"],
        end_reason=orch.end_reason,
        turns=[{"sender": "Claude", "content": "last turn"}],
    )

    assert "**End Reason:** user_ended" in md_report
    assert "**End Reason:** None" not in md_report


def test_degraded_waiting_end_finalizes():
    orch = FakeDegradedOrch()
    with patch("builtins.input", return_value="end"):
        result = _handle_waiting_for_degraded(orch, FakeRenderer())

    assert orch.finalize_mode == "end"
    assert result == "finalized"


def test_degraded_waiting_manual_finalizes_as_manual_mode():
    orch = FakeDegradedOrch()
    with patch("builtins.input", return_value="manual"):
        result = _handle_waiting_for_degraded(orch, FakeRenderer())

    assert orch.finalize_mode == "manual"
    assert result == "finalized"


def test_degraded_waiting_wait_returns_without_finalizing():
    orch = FakeDegradedOrch()
    with patch("builtins.input", return_value="wait"):
        result = _handle_waiting_for_degraded(orch, FakeRenderer())

    assert orch.finalize_mode is None
    assert orch.state == SessionState.WAITING_FOR_USER
    assert result == "deferred"
