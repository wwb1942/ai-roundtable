import pytest
from unittest.mock import patch, MagicMock
from src.plugins.codex_plugin import CodexPlugin
from src.models import RoundContext

def test_validate_success():
    with patch("shutil.which", return_value="/usr/bin/codex"):
        plugin = CodexPlugin()
        plugin.validate()

def test_validate_not_found():
    with patch("shutil.which", return_value=None):
        plugin = CodexPlugin()
        with pytest.raises(RuntimeError, match="codex CLI not found"):
            plugin.validate()

def test_send_turn_uses_exec_subcommand():
    plugin = CodexPlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
    )
    fake_output = 'Analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"test"}\n<<<END_STATUS>>>'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        plugin.send_turn(None, ctx)
        args = mock_run.call_args[0][0]
        assert args[0] == "codex"
        assert args[1] == "exec"
