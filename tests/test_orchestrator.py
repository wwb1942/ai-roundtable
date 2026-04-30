import pytest
from unittest.mock import MagicMock
from unittest.mock import patch
from src.orchestrator import Orchestrator
from src.models import SessionState, ParticipantTurnResult

def make_fake_plugin(pid="fake", status="diverging"):
    plugin = MagicMock()
    plugin.id = pid
    plugin.display_name = pid.title()
    plugin.capabilities = {
        "session_mode": "context-managed", "interruptible": False,
        "structured_status": True, "supports_resume": False, "supports_artifacts": False,
    }
    plugin.validate.return_value = {"ok": True, "errors": [], "warnings": []}
    plugin.start_session.return_value = None
    plugin.send_turn.return_value = ParticipantTurnResult(
        content=f"{pid} says something", raw_output=f"{pid} raw",
        status=status, status_summary="test", artifacts=[], error=None, duration_ms=100,
        session_id=None, token_usage=None, cost_usd=None,
    )
    plugin.close_session.return_value = None
    return plugin

def test_initial_state():
    orch = Orchestrator(topic="test", plugins=[], max_rounds=5)
    assert orch.state == SessionState.READY

def test_requires_min_two_plugins():
    orch = Orchestrator(topic="test", plugins=[make_fake_plugin("a")], max_rounds=5)
    with pytest.raises(RuntimeError, match="at least 2"):
        orch.start()

def test_runs_and_completes_on_convergence():
    p1 = make_fake_plugin("claude", "converging")
    p2 = make_fake_plugin("codex", "converging")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10, convergence_threshold=2)
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.COMPLETED

def test_stops_at_max_rounds():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=3)
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.COMPLETED
    assert orch.round_count == 3

def test_stalemate_triggers_waiting():
    p1 = make_fake_plugin("claude", "stalemate")
    p2 = make_fake_plugin("codex", "stalemate")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10, stalemate_threshold=2)
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.WAITING_FOR_USER

def test_degraded_when_plugin_fails():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    p2.send_turn.return_value = ParticipantTurnResult(
        content="", raw_output="", status="unknown", status_summary=None,
        artifacts=[],
        error={"code": "timeout", "message": "timeout", "detail": None},
        duration_ms=0,
        session_id=None, token_usage=None, cost_usd=None,
    )
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10, max_consecutive_failures=1)
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.WAITING_FOR_USER
    assert orch.end_reason == "degraded"

def test_degraded_waits_for_user_before_finalizing():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    p2.send_turn.return_value = ParticipantTurnResult(
        content="", raw_output="", status="unknown", status_summary=None,
        artifacts=[],
        error={"code": "timeout", "message": "timeout", "detail": None},
        duration_ms=0,
        session_id=None, token_usage=None, cost_usd=None,
    )
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10, max_consecutive_failures=1)
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.WAITING_FOR_USER
    assert orch.end_reason == "degraded"
    p1.close_session.assert_not_called()
    orch.finalize_degraded(mode="manual")
    assert orch.state == SessionState.COMPLETED
    p1.close_session.assert_called_once()

def test_finalize_degraded_emits_user_choice_mode():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    events = []
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1, on_event=events.append)
    orch.start()
    orch._transition_to(SessionState.DEGRADED)
    orch._end_reason = "degraded"
    orch._transition_to(SessionState.WAITING_FOR_USER)
    orch.finalize_degraded(mode="manual")
    assert {"type": "degraded_user_choice", "data": {"mode": "manual"}} in events

def test_finalize_user_ended_completes_waiting_session():
    p1 = make_fake_plugin("claude", "stalemate")
    p2 = make_fake_plugin("codex", "stalemate")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10, stalemate_threshold=1)
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.WAITING_FOR_USER

    orch.finalize_user_ended()

    assert orch.end_reason == "user_ended"
    assert orch.state == SessionState.COMPLETED
    p1.close_session.assert_called_once()
    p2.close_session.assert_called_once()

def test_finalize_user_ended_ignores_non_waiting_session():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10)
    orch.start()

    orch.finalize_user_ended()

    assert orch.end_reason is None
    assert orch.state == SessionState.RUNNING
    p1.close_session.assert_not_called()
    p2.close_session.assert_not_called()

def test_exposes_data_for_report():
    p1 = make_fake_plugin("claude", "converging")
    p2 = make_fake_plugin("codex", "converging")
    orch = Orchestrator(topic="my topic", plugins=[p1, p2], max_rounds=10, convergence_threshold=2)
    orch.start()
    orch.run_auto()
    assert orch.topic == "my topic"
    assert orch.end_reason == "converged"
    assert len(orch.turns) > 0
    assert "Claude" in orch.participant_names

def test_inject_user_input():
    p1 = make_fake_plugin("claude", "stalemate")
    p2 = make_fake_plugin("codex", "stalemate")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10, stalemate_threshold=2)
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.WAITING_FOR_USER
    p1.send_turn.return_value = ParticipantTurnResult(
        content="ok converging now", raw_output="", status="converging",
        status_summary="agreed", artifacts=[], error=None, duration_ms=100,
        session_id=None, token_usage=None, cost_usd=None,
    )
    p2.send_turn.return_value = ParticipantTurnResult(
        content="me too", raw_output="", status="converging",
        status_summary="agreed", artifacts=[], error=None, duration_ms=100,
        session_id=None, token_usage=None, cost_usd=None,
    )
    orch.inject_user_input("Consider team size")
    orch.resume_after_user()
    orch.run_auto()
    assert orch.state == SessionState.COMPLETED

def test_inject_user_input_records_mentions_in_event():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    events = []
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1, on_event=events.append)
    orch.inject_user_input("hello @claude", mentions=["claude"])
    event = events[-1]
    assert event["type"] == "user_input_injected"
    assert event["data"]["mentions"] == ["claude"]

def test_calls_plugin_with_session_and_context():
    p1 = make_fake_plugin("claude", "converging")
    p2 = make_fake_plugin("codex", "converging")
    p1.start_session.return_value = "session-1"
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)
    orch.start()
    orch.run_auto()
    first_call_args = p1.send_turn.call_args.args
    assert first_call_args[0] == "session-1"
    assert first_call_args[1]["speaker_id"] == "claude"

def test_public_run_one_round_calls_plugins():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=2)
    orch.start()
    orch.run_one_round()
    assert orch.round_count == 1
    p1.send_turn.assert_called_once()

def test_emits_progress_events():
    p1 = make_fake_plugin("claude", "converging")
    p2 = make_fake_plugin("codex", "converging")
    events = []
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1, on_event=events.append)
    orch.start()
    orch.run_auto()
    event_types = [event["type"] for event in events]
    assert "turn_prompted" in event_types
    assert "turn_completed" in event_types
    assert "session_completed" in event_types
    assert "state_changed" in event_types

def test_unknown_status_falls_back_to_round_heuristic():
    p1 = make_fake_plugin("claude", "unknown")
    p2 = make_fake_plugin("codex", "unknown")
    p1.send_turn.return_value = ParticipantTurnResult(
        content="We should use a phased migration approach.",
        raw_output="",
        status="unknown",
        status_summary=None,
        artifacts=[],
        error=None,
        duration_ms=100,
        session_id=None,
        token_usage=None,
        cost_usd=None,
    )
    p2.send_turn.return_value = ParticipantTurnResult(
        content="Agreed, a phased migration approach is best.",
        raw_output="",
        status="unknown",
        status_summary=None,
        artifacts=[],
        error=None,
        duration_ms=100,
        session_id=None,
        token_usage=None,
        cost_usd=None,
    )
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)
    orch.start()
    orch.run_auto()
    assert {turn["status"] for turn in orch.turns} == {"converging"}

def test_retry_success_does_not_count_failure():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    p1.send_turn.side_effect = [
        ParticipantTurnResult(
            content="", raw_output="", status="unknown", status_summary=None,
            artifacts=[], error={"code": "timeout", "message": "timeout", "detail": None},
            duration_ms=0, session_id=None, token_usage=None, cost_usd=None,
        ),
        ParticipantTurnResult(
            content="recovered", raw_output="", status="diverging", status_summary=None,
            artifacts=[], error=None, duration_ms=100, session_id=None, token_usage=None, cost_usd=None,
        ),
    ]
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1, retry_count=1)
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.COMPLETED
    assert len([turn for turn in orch.turns if turn["participant_id"] == "claude"]) == 1

def test_retry_exhausted_counts_one_failure_not_one_per_attempt():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    failed = ParticipantTurnResult(
        content="", raw_output="", status="unknown", status_summary=None,
        artifacts=[], error={"code": "timeout", "message": "timeout", "detail": None},
        duration_ms=0, session_id=None, token_usage=None, cost_usd=None,
    )
    p1.send_turn.return_value = failed
    orch = Orchestrator(
        topic="test",
        plugins=[p1, p2],
        max_rounds=1,
        retry_count=2,
        max_consecutive_failures=2,
    )
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.COMPLETED
    assert p1 in orch._plugins

def test_round_heuristic_receives_previous_and_two_current_responses():
    p1 = make_fake_plugin("claude", "unknown")
    p2 = make_fake_plugin("codex", "unknown")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)
    orch.start()
    with patch("src.orchestrator.heuristic_detect", return_value="unknown") as detector:
        orch.run_auto()

    detector.assert_called()
    _, curr_a, response_a, response_b = detector.call_args.args
    assert curr_a == "claude says something"
    assert response_a == "claude says something"
    assert response_b == "codex says something"

def test_round_heuristic_uses_same_participant_previous_content():
    p1 = make_fake_plugin("claude", "unknown")
    p2 = make_fake_plugin("codex", "unknown")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)
    orch._round_count = 2
    orch._all_turns = [
        {"round_number": 1, "participant_id": "codex", "sender": "Codex", "content": "previous codex"},
        {"round_number": 1, "participant_id": "claude", "sender": "Claude", "content": "previous claude"},
    ]
    with patch("src.orchestrator.heuristic_detect", return_value="unknown") as detector:
        orch._analyze_round([
            (p1, ParticipantTurnResult(
                content="current claude", raw_output="", status="unknown", status_summary=None,
                artifacts=[], error=None, duration_ms=100, session_id=None, token_usage=None, cost_usd=None,
            )),
            (p2, ParticipantTurnResult(
                content="current codex", raw_output="", status="unknown", status_summary=None,
                artifacts=[], error=None, duration_ms=100, session_id=None, token_usage=None, cost_usd=None,
            )),
        ])

    detector.assert_called_once()
    prev_same, curr_same, response_a, response_b = detector.call_args.args
    assert prev_same == "previous claude"
    assert curr_same == "current claude"
    assert response_a == "current claude"
    assert response_b == "current codex"
