from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class RoomStore:
    """Append-only room event store shared by MCP clients and renderers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def post(self, author: str, content: str, role: str = "assistant") -> dict:
        event = {
            "id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "message",
            "author": author,
            "role": role,
            "content": content,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def read(self, after_id: str | None = None, limit: int = 50) -> list[dict]:
        events = self._read_all()
        if after_id:
            for index, event in enumerate(events):
                if event.get("id") == after_id:
                    events = events[index + 1 :]
                    break
        return events[-limit:]

    def state(self) -> dict:
        events = self._read_all()
        messages = [event for event in events if event.get("type") == "message"]
        return {
            "event_count": len(events),
            "message_count": len(messages),
            "last_event_id": events[-1]["id"] if events else None,
            "participants": sorted(
                {
                    event["author"]
                    for event in messages
                    if event.get("author") and event.get("role") != "system"
                }
            ),
        }

    def _read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        events: list[dict] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))
        return events
