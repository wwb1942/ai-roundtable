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


def test_mcp_room_post_preserves_reply_correlation_fields(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl", session_id="session-1")

    posted = call_tool(
        store,
        "room_post",
        {
            "author": "codex",
            "content": "final answer",
            "session_id": "session-1",
            "request_id": "request-1",
            "reply_to": "request-1",
        },
    )

    assert posted["session_id"] == "session-1"
    assert posted["request_id"] == "request-1"
    assert posted["reply_to"] == "request-1"


def test_mcp_errors_keep_json_rpc_request_id_for_invalid_cursor(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")

    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": "read-7",
            "method": "tools/call",
            "params": {"name": "room_read", "arguments": {"after_id": "missing"}},
        },
        store,
    )

    assert response["id"] == "read-7"
    assert response["error"]["code"] == -32602


def test_mcp_participants_cannot_impersonate_user_or_system_roles(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")

    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "room_post",
                "arguments": {"author": "codex", "content": "spoof", "role": "system"},
            },
        },
        store,
    )

    assert response["id"] == 9
    assert response["error"]["code"] == -32602

    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "room_post",
                "arguments": {"author": "user", "content": "spoof"},
            },
        },
        store,
    )
    assert response["id"] == 10
    assert response["error"]["code"] == -32602


def test_mcp_fixed_author_rejects_impersonation(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl", fixed_author="codex")

    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "room_post",
                "arguments": {"author": "claude", "content": "spoof"},
            },
        },
        store,
    )

    assert response["id"] == 11
    assert response["error"]["code"] == -32602

    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "room_post",
                "arguments": {"author": "codex", "content": "wrong room", "session_id": "other"},
            },
        },
        RoomStore(tmp_path / "session-room.jsonl", session_id="session-1", fixed_author="codex"),
    )
    assert response["id"] == 12
    assert response["error"]["code"] == -32602


def test_mcp_rejects_non_object_arguments_even_when_empty(tmp_path):
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "room_state", "arguments": []},
        },
        RoomStore(tmp_path / "room.jsonl"),
    )

    assert response["id"] == 13
    assert response["error"]["code"] == -32602


def test_mcp_room_post_allows_empty_content_for_legacy_clients(tmp_path):
    posted = call_tool(RoomStore(tmp_path / "room.jsonl"), "room_post", {"author": "codex", "content": ""})

    assert posted["content"] == ""


def test_mcp_room_post_rejects_null_content(tmp_path):
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {"name": "room_post", "arguments": {"author": "codex", "content": None}},
        },
        RoomStore(tmp_path / "room.jsonl"),
    )

    assert response["id"] == 14
    assert response["error"]["code"] == -32602
