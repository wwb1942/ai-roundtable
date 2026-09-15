from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from uuid import uuid4

from src.models import EventRecord
from src.room_store import jsonl_file_lock

SCHEMA_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_LOGGER = logging.getLogger(__name__)
MAX_EVENT_LINE_BYTES = 6_000_000

class EventLog:
    def __init__(self, base_dir: str, session_id: str):
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("Invalid session_id for event log path")
        self._path = Path(base_dir) / f"{session_id}.jsonl"
        self._session_id = session_id
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, data: dict) -> EventRecord:
        record: EventRecord = {
            "schema_version": SCHEMA_VERSION,
            "id": str(uuid4()),
            "session_id": self._session_id,
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        if len(serialized.encode("utf-8")) > MAX_EVENT_LINE_BYTES:
            raise ValueError(f"event exceeds the maximum size of {MAX_EVENT_LINE_BYTES} bytes")
        with jsonl_file_lock(self._path):
            with open(self._path, "a", encoding="utf-8", newline="") as f:
                f.write(serialized)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    # Some virtual filesystems do not expose a durable fd.
                    pass
        return record

    def read_all(self) -> list[EventRecord]:
        records: list[EventRecord] = []
        with jsonl_file_lock(self._path):
            try:
                with open(self._path, "rb") as f:
                    for line_number, raw_line in enumerate(f, start=1):
                        if len(raw_line) > MAX_EVENT_LINE_BYTES:
                            _LOGGER.warning("Skipping oversized event at %s:%d", self._path, line_number)
                            continue
                        line = (
                            raw_line.decode("utf-8", errors="replace")
                            if isinstance(raw_line, bytes)
                            else str(raw_line)
                        )
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError as exc:
                            _LOGGER.warning("Skipping malformed event at %s:%d: %s", self._path, line_number, exc)
                            continue
                        if isinstance(record, dict):
                            records.append(cast(EventRecord, record))
            except FileNotFoundError:
                return []
        return records

    def read_after(self, index: int) -> list[EventRecord]:
        if index < 0:
            raise ValueError("index must be non-negative")
        return self.read_all()[index:]

    @property
    def path(self) -> Path:
        return self._path
