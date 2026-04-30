import json

from scripts import generate_mcp_config
from scripts.generate_mcp_config import build_server_config
from scripts.generate_mcp_config import write_claude_mcp_json
from scripts.generate_mcp_config import write_codex_install_script
from scripts.generate_mcp_config import write_codex_snippet


def test_build_server_config_points_to_room_server(tmp_path):
    config = build_server_config(tmp_path / "python.exe", tmp_path / "room.jsonl")

    assert config["command"].endswith("python.exe")
    assert "room_mcp_server.py" in config["args"][0]
    assert config["args"][-1].endswith("room.jsonl")
    assert config["env"] == {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def test_write_claude_mcp_json(tmp_path):
    path = tmp_path / ".mcp.json"
    server = {"command": "python", "args": ["server.py"]}

    write_claude_mcp_json(path, server)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["ai-roundtable-room"] == server


def test_write_codex_snippet(tmp_path):
    path = tmp_path / "snippet.toml"
    server = {"command": "python", "args": ["server.py", "--room-file", "room.jsonl"]}

    write_codex_snippet(path, server)

    content = path.read_text(encoding="utf-8")
    assert "[mcp_servers.ai-roundtable-room]" in content
    assert "command = 'python'" in content
    assert "'server.py'" in content
    assert "[mcp_servers.ai-roundtable-room.env]" in content
    assert "PYTHONUTF8 = '1'" in content


def test_write_codex_install_script(tmp_path):
    path = tmp_path / "install.ps1"

    write_codex_install_script(path, tmp_path / "snippet.toml")

    content = path.read_text(encoding="utf-8")
    assert ".codex\\config.toml" in content
    assert "ai-roundtable-room mcp" in content


def test_install_codex_config_adds_managed_block(tmp_path):
    config_path = tmp_path / "config.toml"
    snippet_path = tmp_path / "snippet.toml"
    snippet_path.write_text("[mcp_servers.ai-roundtable-room]\ncommand = 'python'\n", encoding="utf-8")

    generate_mcp_config.install_codex_config(config_path, snippet_path)

    content = config_path.read_text(encoding="utf-8")
    assert "# >>> ai-roundtable-room mcp >>>" in content
    assert "[mcp_servers.ai-roundtable-room]" in content


def test_install_codex_config_replaces_existing_managed_block(tmp_path):
    config_path = tmp_path / "config.toml"
    snippet_path = tmp_path / "snippet.toml"
    config_path.write_text(
        "model = 'gpt-5'\n\n# >>> ai-roundtable-room mcp >>>\nold\n# <<< ai-roundtable-room mcp <<<\n",
        encoding="utf-8",
    )
    snippet_path.write_text("new\n", encoding="utf-8")

    generate_mcp_config.install_codex_config(config_path, snippet_path)

    content = config_path.read_text(encoding="utf-8")
    assert "model = 'gpt-5'" in content
    assert "new" in content
    assert "old" not in content
