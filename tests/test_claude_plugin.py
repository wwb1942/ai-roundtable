import pytest
from unittest.mock import patch, MagicMock
from src.plugins.claude_plugin import ClaudePlugin
from src.models import RoundContext

def test_validate_success():
    with patch("shutil.which", return_value="/usr/bin/claude"):
        plugin = ClaudePlugin()
        plugin.validate()

def test_validate_not_found():
    with patch("shutil.which", return_value=None):
        plugin = ClaudePlugin()
        with pytest.raises(RuntimeError, match="claude CLI not found"):
            plugin.validate()

def test_send_turn_returns_result():
    plugin = ClaudePlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
    )
    fake_output = 'My analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"initial"}\n<<<END_STATUS>>>'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        result = plugin.send_turn(None, ctx)
        assert result["status"] == "diverging"
        assert "My analysis" in result["content"]
        assert result["error"] is None

def test_send_turn_timeout():
    import subprocess
    plugin = ClaudePlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
    )
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 120)):
        result = plugin.send_turn(None, ctx)
        assert result["error"] is not None
        assert result["status"] == "unknown"
