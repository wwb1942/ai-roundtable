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
from src.room_store import DEFAULT_READ_LIMIT


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
                    "author": {"type": "string", "maxLength": 128},
                    "content": {"type": "string", "maxLength": 1000000},
                    "role": {"type": "string", "enum": ["assistant"], "default": "assistant"},
                    "session_id": {"type": "string", "maxLength": 128},
                    "request_id": {"type": "string", "maxLength": 128},
                    "reply_to": {"type": "string", "maxLength": 128},
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
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
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
    if not isinstance(request, dict):
        return _error_response(None, -32600, "request must be an object")
    if request.get("jsonrpc") not in (None, "2.0"):
        return _error_response(request.get("id"), -32600, "jsonrpc must be '2.0'")
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
        if not isinstance(params, dict):
            return _error_response(request_id, -32602, "params must be an object")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error_response(request_id, -32602, "tool arguments must be an object")
        try:
            result = call_tool(store, name, arguments)
        except KeyError as exc:
            return _error_response(request_id, -32602, f"missing required argument: {exc.args[0]}")
        except (TypeError, ValueError) as exc:
            return _error_response(request_id, -32602, str(exc))
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


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def call_tool(store: RoomStore, name: str, arguments: dict[str, Any]) -> dict:
    if name == "room_post":
        if "author" not in arguments or "content" not in arguments:
            missing = "author" if "author" not in arguments else "content"
            raise KeyError(missing)
        author = _required_string(arguments, "author")
        if author.lower() in {"user", "system"}:
            raise ValueError("reserved authors cannot post through the participant MCP tool")
        role = _optional_string(arguments, "role") or "assistant"
        if role != "assistant":
            raise ValueError("MCP participants may only post with role=assistant")
        return store.post(
            author=author,
            content=_required_string(arguments, "content", allow_empty=True),
            role=role,
            session_id=_optional_string(arguments, "session_id"),
            request_id=_optional_string(arguments, "request_id"),
            reply_to=_optional_string(arguments, "reply_to"),
        )
    if name == "room_read":
        limit = arguments.get("limit", DEFAULT_READ_LIMIT)
        if limit is None:
            limit = DEFAULT_READ_LIMIT
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be a positive integer")
        after_id = arguments.get("after_id")
        if after_id is not None and not isinstance(after_id, str):
            raise ValueError("after_id must be a string or null")
        return {"events": store.read(after_id=after_id, limit=limit, session_id=store.session_id)}
    if name == "room_state":
        return store.state(session_id=store.session_id)
    raise ValueError(f"Unknown tool: {name}")


def _optional_string(arguments: dict[str, Any], name: str, *, allow_empty: bool = False) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{name} must be a non-empty string when provided")
    return value


def _required_string(arguments: dict[str, Any], name: str, *, allow_empty: bool = False) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{name} must be a non-empty string")
    return value


def serve(store: RoomStore) -> None:
    configure_stdio()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request: Any = None
        try:
            request = json.loads(line)
            response = handle_request(request, store)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc.msg}"},
            }
        except Exception as exc:  # MCP clients need structured errors, not tracebacks.
            request_id = request.get("id") if isinstance(request, dict) else None
            message = str(exc) if isinstance(exc, (ValueError, TypeError, KeyError)) else "Internal MCP server error"
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": message},
            }
        if response is not None:
            print(serialize_response(response), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server for the AI roundtable room.")
    parser.add_argument("--room-file", default=str(PROJECT_ROOT / "logs" / "mcp-room.jsonl"))
    parser.add_argument("--session", default="")
    parser.add_argument("--author", default="")
    args = parser.parse_args()
    serve(
        RoomStore(
            Path(args.room_file),
            session_id=args.session or None,
            fixed_author=args.author or None,
        )
    )


if __name__ == "__main__":
    main()
