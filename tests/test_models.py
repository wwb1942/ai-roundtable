import pytest
from src.models import (
    ParticipantCapabilities, RoundContext,
    EventRecord, SessionState, RecentTurn, ParticipantError,
    IllegalStateTransition, assert_transition_allowed,
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
        system_contract="rules here",
        history_summary="",
        recent_turns=[],
        user_inputs=[],
        turn_instruction="Give your opinion.",
        speaker_id="claude",
        mentioned_by_user=False,
    )
    assert ctx["round_number"] == 1
    assert ctx["system_contract"] == "rules here"
    assert ctx["speaker_id"] == "claude"

def test_recent_turn_status_optional():
    turn_with = RecentTurn(round_number=1, participant_id="claude", content="hi", status="converging")
    assert turn_with["status"] == "converging"
    turn_without = RecentTurn(round_number=1, participant_id="claude", content="hi")
    assert "status" not in turn_without

def test_participant_error_codes_include_plugin_runtime_errors():
    err = ParticipantError(code="command_not_found", message="missing", detail=None)
    assert err["code"] == "command_not_found"
    err2 = ParticipantError(code="protocol_violation", message="bad response", detail=None)
    assert err2["code"] == "protocol_violation"

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

def test_illegal_state_transition_raises():
    with pytest.raises(IllegalStateTransition):
        assert_transition_allowed(SessionState.COMPLETED, SessionState.RUNNING)

def test_legal_state_transition_passes():
    assert_transition_allowed(SessionState.READY, SessionState.RUNNING)
