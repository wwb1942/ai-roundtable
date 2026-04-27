from __future__ import annotations
from enum import Enum
from typing import TypedDict, Literal

class ParticipantCapabilities(TypedDict):
    session_mode: Literal["context-managed", "session-managed"]
    interruptible: bool
    structured_status: bool
    supports_resume: bool
    supports_artifacts: bool

class ParticipantTurnResult(TypedDict):
    content: str
    raw_output: str
    status: Literal["converging", "diverging", "stalemate", "unknown"]
    status_summary: str | None
    artifacts: list[str]
    error: str | None
    duration_ms: int

class RoundContext(TypedDict):
    round_number: int
    topic: str
    history_summary: str
    recent_turns: list[dict]
    user_inputs: list[str]
    turn_instruction: str

class EventRecord(TypedDict):
    schema_version: int
    type: str
    timestamp: str
    data: dict

class SessionState(str, Enum):
    READY = "ready"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEGRADED = "degraded"
