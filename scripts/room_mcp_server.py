from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.room_store import RoomStore


def configure_stdio() -> None:
    """Use UTF-8 stdio for MCP JSON-RPC on Windows and WSL."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def serialize_response(response: dict[str, Any]) -> str:
    return json.dumps(response, ensure_ascii=True)


def tool_definitions() -> list[dict]:
    return [
        {
            "name": "room_post",
            "description": "Post a final visible reply to the shared AI roundtable room.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "author": {"type": "string"},
                    "content": {"type": "string"},
                    "role": {"type": "string", "default": "assistant"},
                },
                "required": ["author", "content"],
            },
        },
        {
            "name": "room_read",
            "description": "Read recent shared room messages.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "after_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
        {
            "name": "room_state",
            "description": "Get room state including last event id and participants.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def handle_request(request: dict[str, Any], store: RoomStore) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ai-roundtable-room", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tool_definitions()}}
    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments") or {}
        result = call_tool(store, name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def call_tool(store: RoomStore, name: str, arguments: dict[str, Any]) -> dict:
    if name == "room_post":
        return store.post(
            author=str(arguments["author"]),
            content=str(arguments["content"]),
            role=str(arguments.get("role") or "assistant"),
        )
    if name == "room_read":
        return {"events": store.read(after_id=arguments.get("after_id"), limit=int(arguments.get("limit") or 50))}
    if name == "room_state":
        return store.state()
    raise ValueError(f"Unknown tool: {name}")


def serve(store: RoomStore) -> None:
    configure_stdio()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            response = handle_request(json.loads(line), store)
        except Exception as exc:  # MCP clients need structured errors, not tracebacks.
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32000, "message": str(exc)},
            }
        if response is not None:
            print(serialize_response(response), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server for the AI roundtable room.")
    parser.add_argument("--room-file", default=str(PROJECT_ROOT / "logs" / "mcp-room.jsonl"))
    args = parser.parse_args()
    serve(RoomStore(Path(args.room_file)))


if __name__ == "__main__":
    main()
