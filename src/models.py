from __future__ import annotations
from enum import Enum
from typing import TypedDict, Literal, NotRequired

class ParticipantCapabilities(TypedDict):
    session_mode: Literal["context-managed", "session-managed"]
    interruptible: bool
    structured_status: bool
    supports_resume: bool
    supports_artifacts: bool

class ValidationResult(TypedDict):
    ok: bool
    errors: list[str]
    warnings: list[str]

class ParticipantError(TypedDict):
    code: Literal[
        "timeout",
        "non_zero_exit",
        "parse_failed",
        "interrupted",
        "unknown",
        "command_not_found",
        "protocol_violation",
    ]
    message: str
    detail: str | None

class TokenUsage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ParticipantTurnResult(TypedDict):
    content: str
    raw_output: str
    status: Literal["converging", "diverging", "stalemate", "unknown"]
    status_summary: str | None
    artifacts: list[str]
    error: ParticipantError | None
    duration_ms: int
    session_id: NotRequired[str | None]
    token_usage: NotRequired[TokenUsage | None]
    cost_usd: NotRequired[float | None]

class RecentTurn(TypedDict):
    round_number: int
    participant_id: str
    content: str
    status: NotRequired[Literal["converging", "diverging", "stalemate", "unknown"]]

class RoundContext(TypedDict):
    round_number: int
    topic: str
    system_contract: str
    history_summary: str
    recent_turns: list[RecentTurn]
    user_inputs: list[str]
    turn_instruction: str
    speaker_id: str
    mentioned_by_user: bool

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


class IllegalStateTransition(RuntimeError):
    pass


ALLOWED_STATE_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.READY: {SessionState.RUNNING, SessionState.FAILED},
    SessionState.RUNNING: {
        SessionState.WAITING_FOR_USER,
        SessionState.SUMMARIZING,
        SessionState.DEGRADED,
        SessionState.FAILED,
    },
    SessionState.WAITING_FOR_USER: {SessionState.RUNNING, SessionState.SUMMARIZING, SessionState.FAILED},
    SessionState.DEGRADED: {SessionState.WAITING_FOR_USER, SessionState.SUMMARIZING, SessionState.FAILED},
    SessionState.SUMMARIZING: {SessionState.COMPLETED, SessionState.FAILED},
    SessionState.COMPLETED: set(),
    SessionState.FAILED: set(),
}


def assert_transition_allowed(current: SessionState, next_state: SessionState) -> None:
    if next_state == current:
        return
    if next_state not in ALLOWED_STATE_TRANSITIONS[current]:
        raise IllegalStateTransition(f"Illegal state transition: {current.value} -> {next_state.value}")
