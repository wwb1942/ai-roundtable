from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_start_script_installs_codex_mcp_config():
    script = (PROJECT_ROOT / "scripts" / "start-tmux-roundtable.ps1").read_text(encoding="utf-8")

    assert "& powershell -ExecutionPolicy Bypass -File $CodexMcpInstall" in script


def test_start_script_does_not_abort_when_codex_mcp_install_fails():
    script = (PROJECT_ROOT / "scripts" / "start-tmux-roundtable.ps1").read_text(encoding="utf-8")

    assert "try {" in script
    assert "catch {" in script
    assert "Codex MCP config install failed" in script


def test_start_script_rebuilds_stale_virtualenv_interpreter():
    script = (PROJECT_ROOT / "scripts" / "start-tmux-roundtable.ps1").read_text(encoding="utf-8")

    assert "$PythonHealthy = $false" in script
    assert 'import sys; print(sys.version_info[:2])' in script
    assert "python -m venv --clear" in script


def test_start_script_lets_python_launcher_build_default_agent_commands():
    script = (PROJECT_ROOT / "scripts" / "start-tmux-roundtable.ps1").read_text(encoding="utf-8")

    assert '[string]$ClaudeCommand = ""' in script
    assert '[string]$CodexCommand = ""' in script
    assert 'cmd.exe /k codex' not in script
    assert 'cmd.exe /k claude' not in script


def test_start_script_forwards_explicit_unsafe_codex_opt_in():
    script = (PROJECT_ROOT / "scripts" / "start-tmux-roundtable.ps1").read_text(encoding="utf-8")

    assert '[switch]$AllowUnsafeCodex' in script
    assert '"--allow-unsafe-codex"' in script
