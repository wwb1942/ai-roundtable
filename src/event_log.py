from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from src.models import EventRecord

SCHEMA_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

class EventLog:
    def __init__(self, base_dir: str, session_id: str):
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("Invalid session_id for event log path")
        self._path = Path(base_dir) / f"{session_id}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, data: dict) -> EventRecord:
        record: EventRecord = {
            "schema_version": SCHEMA_VERSION,
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def read_all(self) -> list[EventRecord]:
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def read_after(self, index: int) -> list[EventRecord]:
        all_events = self.read_all()
        return all_events[index:]

    @property
    def path(self) -> Path:
        return self._path
