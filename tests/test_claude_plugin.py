import pytest
from unittest.mock import patch, MagicMock
from src.plugins.claude_plugin import ClaudePlugin
from src.models import RoundContext

def test_validate_success():
    with patch("shutil.which", return_value="/usr/bin/claude"):
        plugin = ClaudePlugin()
        result = plugin.validate()
        assert result["ok"] is True

def test_validate_not_found():
    with patch("shutil.which", return_value=None):
        plugin = ClaudePlugin()
        result = plugin.validate()
        assert result["ok"] is False
        assert "claude CLI not found" in result["errors"][0]

def test_send_turn_returns_result():
    plugin = ClaudePlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="claude", mentioned_by_user=False,
    )
    fake_output = 'My analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"initial"}\n<<<END_STATUS>>>'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        result = plugin.send_turn(None, ctx)
        assert result["status"] == "diverging"
        assert "My analysis" in result["content"]
        assert result["error"] is None

def test_send_turn_uses_configured_args():
    plugin = ClaudePlugin(command="claude-custom", args=["--print"])
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="claude", mentioned_by_user=False,
    )
    fake_output = 'My analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"initial"}\n<<<END_STATUS>>>'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        plugin.send_turn(None, ctx)
        args = mock_run.call_args[0][0]
        assert args[:2] == ["claude-custom", "--print"]
        assert args[-1].startswith("Share your view.") is False

def test_send_turn_resolves_command_before_running():
    plugin = ClaudePlugin(command="claude")
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="claude", mentioned_by_user=False,
    )
    fake_output = 'My analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"initial"}\n<<<END_STATUS>>>'
    with patch("shutil.which", return_value="C:\\Tools\\claude.cmd"), patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        plugin.send_turn(None, ctx)
        args = mock_run.call_args[0][0]
        assert args[0] == "C:\\Tools\\claude.cmd"

def test_send_turn_file_not_found_returns_structured_error():
    plugin = ClaudePlugin(command="claude")
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="claude", mentioned_by_user=False,
    )
    with patch("subprocess.run", side_effect=FileNotFoundError("missing")):
        result = plugin.send_turn(None, ctx)
        assert result["status"] == "unknown"
        assert result["error"]["code"] == "command_not_found"

def test_build_prompt_tells_model_to_answer_topic_now():
    plugin = ClaudePlugin()
    ctx = RoundContext(
        round_number=1, topic="Improve the roundtable system", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="contract", speaker_id="claude", mentioned_by_user=False,
    )
    prompt = plugin._build_prompt(ctx)
    assert "THIS IS THE TOPIC TO ANSWER NOW" in prompt
    assert "Do not say you are ready" in prompt
    assert "Improve the roundtable system" in prompt

def test_ready_only_response_is_protocol_error():
    plugin = ClaudePlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="claude", mentioned_by_user=False,
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="I'm ready. Send me the topic.", stderr="", returncode=0)
        result = plugin.send_turn(None, ctx)
        assert result["error"]["code"] == "protocol_violation"

def test_missing_status_block_is_not_turn_error():
    plugin = ClaudePlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="claude", mentioned_by_user=False,
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="A real answer without a status block.", stderr="", returncode=0)
        result = plugin.send_turn(None, ctx)
        assert result["status"] == "unknown"
        assert result["error"] is None

def test_unknown_constructor_kwargs_are_rejected():
    with pytest.raises(TypeError):
        ClaudePlugin(typo=True)

def test_send_turn_timeout():
    import subprocess
    plugin = ClaudePlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="claude", mentioned_by_user=False,
    )
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 120)):
        result = plugin.send_turn(None, ctx)
        assert result["error"] is not None
        assert result["status"] == "unknown"
