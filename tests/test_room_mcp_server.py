from scripts.room_mcp_server import call_tool
from scripts.room_mcp_server import handle_request
from scripts.room_mcp_server import serialize_response
from src.room_store import RoomStore


def test_mcp_lists_room_tools(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")

    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, store)

    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == ["room_post", "room_read", "room_state"]


def test_mcp_room_post_and_read(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")

    posted = call_tool(store, "room_post", {"author": "codex", "content": "final answer"})
    read = call_tool(store, "room_read", {"limit": 10})

    assert posted["author"] == "codex"
    assert read["events"][0]["content"] == "final answer"


def test_mcp_initialize_advertises_tools(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")

    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, store)

    assert response["result"]["capabilities"] == {"tools": {}}
    assert response["result"]["serverInfo"]["name"] == "ai-roundtable-room"


def test_mcp_response_serialization_is_ascii_safe_for_unicode_room_content(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "room_post",
                "arguments": {"author": "Claude", "content": "Claude 已收到"},
            },
        },
        store,
    )

    serialized = serialize_response(response)

    serialized.encode("ascii")
    assert "Claude" in serialized
