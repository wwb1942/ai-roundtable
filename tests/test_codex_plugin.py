import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from src.plugins.codex_plugin import CodexPlugin
from src.models import RoundContext

def test_validate_success():
    with patch("shutil.which", return_value="/usr/bin/codex"):
        plugin = CodexPlugin()
        result = plugin.validate()
        assert result["ok"] is True

def test_validate_not_found():
    with patch("shutil.which", return_value=None):
        plugin = CodexPlugin()
        result = plugin.validate()
        assert result["ok"] is False
        assert "codex CLI not found" in result["errors"][0]

def test_send_turn_uses_exec_subcommand():
    plugin = CodexPlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="codex", mentioned_by_user=False,
    )
    fake_output = 'Analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"test"}\n<<<END_STATUS>>>'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        plugin.send_turn(None, ctx)
        args = mock_run.call_args[0][0]
        assert Path(args[0]).name.lower() in {"codex", "codex.exe", "codex.cmd"}
        assert "exec" in args

def test_send_turn_uses_configured_subcommand_and_args():
    plugin = CodexPlugin(command="codex-custom", subcommand="exec", args=["--sandbox", "workspace-write"])
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="codex", mentioned_by_user=False,
    )
    fake_output = 'Analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"test"}\n<<<END_STATUS>>>'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        plugin.send_turn(None, ctx)
        args = mock_run.call_args[0][0]
        assert args[:4] == ["codex-custom", "exec", "--sandbox", "workspace-write"]

def test_send_turn_resolves_command_before_running():
    plugin = CodexPlugin(command="codex")
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="codex", mentioned_by_user=False,
    )
    fake_output = 'Analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"test"}\n<<<END_STATUS>>>'
    with patch("shutil.which", return_value="C:\\Tools\\codex.cmd"), patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        plugin.send_turn(None, ctx)
        args = mock_run.call_args[0][0]
        assert args[0] == "C:\\Tools\\codex.cmd"

def test_send_turn_file_not_found_returns_structured_error():
    plugin = CodexPlugin(command="codex")
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="codex", mentioned_by_user=False,
    )
    with patch("subprocess.run", side_effect=FileNotFoundError("missing")):
        result = plugin.send_turn(None, ctx)
        assert result["status"] == "unknown"
        assert result["error"]["code"] == "command_not_found"

def test_build_prompt_tells_model_to_answer_topic_now():
    plugin = CodexPlugin()
    ctx = RoundContext(
        round_number=1, topic="Improve the roundtable system", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="contract", speaker_id="codex", mentioned_by_user=False,
    )
    prompt = plugin._build_prompt(ctx)
    assert "THIS IS THE TOPIC TO ANSWER NOW" in prompt
    assert "Do not say you are ready" in prompt
    assert "Improve the roundtable system" in prompt

def test_ready_only_response_is_protocol_error():
    plugin = CodexPlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="codex", mentioned_by_user=False,
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="Ready to participate. Send the topic.", stderr="", returncode=0)
        result = plugin.send_turn(None, ctx)
        assert result["error"]["code"] == "protocol_violation"

def test_missing_status_block_is_not_turn_error():
    plugin = CodexPlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
        system_contract="", speaker_id="codex", mentioned_by_user=False,
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="A real answer without a status block.", stderr="", returncode=0)
        result = plugin.send_turn(None, ctx)
        assert result["status"] == "unknown"
        assert result["error"] is None

def test_unknown_constructor_kwargs_are_rejected():
    with pytest.raises(TypeError):
        CodexPlugin(typo=True)
