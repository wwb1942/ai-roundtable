from __future__ import annotations

import logging
import json
import os
import threading
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


DEFAULT_READ_LIMIT = 50
MAX_READ_LIMIT = 1000
MAX_AUTHOR_LENGTH = 128
MAX_ROLE_LENGTH = 32
MAX_CONTENT_LENGTH = 1_000_000
MAX_CONTENT_BYTES = MAX_CONTENT_LENGTH * 4
MAX_CORRELATION_LENGTH = 128
# JSON escaping can expand control-heavy content to roughly six bytes per
# character; leave room for the envelope so valid max-size messages survive a
# UTF-8 byte-level read cap.
MAX_LINE_BYTES = MAX_CONTENT_LENGTH * 6 + 16_384
SCHEMA_VERSION = 1
_LOGGER = logging.getLogger(__name__)
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class UnknownCursorError(ValueError):
    """Raised when a read cursor does not identify an event in this room."""

    def __init__(self, cursor: str) -> None:
        self.cursor = cursor
        super().__init__(f"Unknown room event cursor: {cursor}")


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be a positive integer")
    if limit <= 0:
        raise ValueError("limit must be a positive integer")
    return min(limit, MAX_READ_LIMIT)


def _validate_text(value: str, name: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds the maximum length of {maximum}")
    return value


def _process_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def jsonl_file_lock(path: Path):
    """Lock a small sidecar file across threads and processes on both platforms."""

    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    process_lock = _process_lock(lock_path)
    with process_lock:
        with open(lock_path, "a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


class RoomStore:
    """Append-only room event store shared by MCP clients and renderers."""

    def __init__(
        self,
        path: Path,
        session_id: str | None = None,
        fixed_author: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.session_id = _validate_text(session_id, "session_id", MAX_CORRELATION_LENGTH) if session_id else None
        self.fixed_author = _validate_text(fixed_author, "fixed_author", MAX_AUTHOR_LENGTH) if fixed_author else None
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def post(
        self,
        author: str,
        content: str,
        role: str = "assistant",
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        reply_to: str | None = None,
        event_type: str = "message",
    ) -> dict:
        author = _validate_text(author, "author", MAX_AUTHOR_LENGTH)
        if self.fixed_author is not None and author.lower() != self.fixed_author.lower():
            raise ValueError(f"author is fixed to {self.fixed_author!r} for this room client")
        content = _validate_text(content, "content", MAX_CONTENT_LENGTH, allow_empty=True)
        role = _validate_text(role, "role", MAX_ROLE_LENGTH)
        event_type = _validate_text(event_type, "event_type", MAX_ROLE_LENGTH)
        if role not in {"user", "assistant", "system"}:
            raise ValueError("role must be one of: user, assistant, system")
        if session_id is not None:
            session_id = _validate_text(session_id, "session_id", MAX_CORRELATION_LENGTH)
            if self.session_id is not None and session_id != self.session_id:
                raise ValueError(f"session_id is fixed to {self.session_id!r} for this room client")
        if request_id is not None:
            request_id = _validate_text(request_id, "request_id", MAX_CORRELATION_LENGTH)
        if reply_to is not None:
            reply_to = _validate_text(reply_to, "reply_to", MAX_CORRELATION_LENGTH)
        event = {
            "schema_version": SCHEMA_VERSION,
            "id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "author": author,
            "role": role,
            "content": content,
        }
        resolved_session = session_id if session_id is not None else self.session_id
        # Keep the envelope shape stable for consumers while allowing old
        # JSONL records (which lack these optional fields) to remain readable.
        event["session_id"] = resolved_session
        event["request_id"] = request_id
        event["reply_to"] = reply_to
        serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with jsonl_file_lock(self.path):
            with open(self.path, "a", encoding="utf-8", newline="") as f:
                f.write(serialized)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    # Some virtual filesystems and test streams do not expose a
                    # durable file descriptor; the lock still preserves framing.
                    pass
        return event

    def read(
        self,
        after_id: str | None = None,
        limit: int = DEFAULT_READ_LIMIT,
        *,
        session_id: str | None = None,
    ) -> list[dict]:
        effective_limit = _validate_limit(limit)
        if session_id is not None:
            session_id = _validate_text(session_id, "session_id", MAX_CORRELATION_LENGTH)

        def belongs_to_session(event: dict) -> bool:
            return session_id is None or event.get("session_id") in (None, session_id)

        if after_id is None:
            # Keep only the tail needed by a normal "recent messages" read;
            # polling a long-lived room should not materialize the whole log.
            recent: deque[dict] = deque(maxlen=effective_limit)
            iterator = self._iter_events()
            try:
                recent.extend(event for event in iterator if belongs_to_session(event))
            finally:
                iterator.close()
            return list(recent)

        found = False
        page: list[dict] = []
        iterator = self._iter_events()
        try:
            for event in iterator:
                if not found:
                    if event.get("id") == after_id:
                        found = True
                    continue
                if not belongs_to_session(event):
                    continue
                page.append(event)
                if len(page) >= effective_limit:
                    break
        finally:
            iterator.close()
        if not found:
            raise UnknownCursorError(after_id)
        return page

    def find(self, event_id: str) -> dict | None:
        """Return an event by id without changing cursor semantics."""

        iterator = self._iter_events()
        try:
            for event in iterator:
                if event.get("id") == event_id:
                    return event
        finally:
            iterator.close()
        return None

    def state(self, *, session_id: str | None = None) -> dict:
        if session_id is not None:
            session_id = _validate_text(session_id, "session_id", MAX_CORRELATION_LENGTH)
        event_count = 0
        message_count = 0
        last_event_id: str | None = None
        participants: set[str] = set()
        iterator = self._iter_events()
        try:
            for event in iterator:
                if session_id is not None and event.get("session_id") not in (None, session_id):
                    continue
                event_count += 1
                last_event_id = event.get("id")
                if event.get("type") != "message":
                    continue
                message_count += 1
                author = event.get("author")
                if isinstance(author, str) and author and event.get("role") != "system":
                    participants.add(author)
        finally:
            iterator.close()
        return {
            "event_count": event_count,
            "message_count": message_count,
            "last_event_id": last_event_id,
            "participants": sorted(participants),
        }

    def _read_all(self) -> list[dict]:
        return list(self._iter_events())

    def _iter_events(self):
        with jsonl_file_lock(self.path):
            try:
                with open(self.path, "rb") as f:
                    for line_number, raw_line in enumerate(f, start=1):
                        if len(raw_line) > MAX_LINE_BYTES:
                            _LOGGER.warning("Skipping oversized room event at %s:%d", self.path, line_number)
                            continue
                        line = (
                            raw_line.decode("utf-8", errors="replace")
                            if isinstance(raw_line, bytes)
                            else str(raw_line)
                        ).strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                            # A process can be observed while it is writing its
                            # final line. Keep committed events readable and make
                            # corruption visible instead of failing the whole room.
                            _LOGGER.warning("Skipping malformed room event at %s:%d: %s", self.path, line_number, exc)
                            continue
                        if not isinstance(event, dict):
                            _LOGGER.warning("Skipping non-object room event at %s:%d", self.path, line_number)
                            continue
                        if not isinstance(event.get("id"), str) or not event["id"]:
                            _LOGGER.warning("Skipping room event without a valid id at %s:%d", self.path, line_number)
                            continue
                        yield event
            except FileNotFoundError:
                return
