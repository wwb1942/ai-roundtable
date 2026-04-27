import pytest
from src.models import (
    ParticipantCapabilities, ParticipantTurnResult, RoundContext,
    EventRecord, SessionState
)

def test_participant_capabilities_valid():
    caps = ParticipantCapabilities(
        session_mode="context-managed",
        interruptible=False,
        structured_status=True,
        supports_resume=False,
        supports_artifacts=False,
    )
    assert caps["session_mode"] == "context-managed"

def test_round_context_has_required_fields():
    ctx = RoundContext(
        round_number=1,
        topic="test topic",
        history_summary="",
        recent_turns=[],
        user_inputs=[],
        turn_instruction="Give your opinion.",
    )
    assert ctx["round_number"] == 1

def test_event_record_has_schema_version():
    event = EventRecord(
        schema_version=1,
        type="session_started",
        timestamp="2026-04-24T10:00:00Z",
        data={"topic": "test"},
    )
    assert event["schema_version"] == 1

def test_session_state_values():
    assert SessionState.READY == "ready"
    assert SessionState.DEGRADED == "degraded"
