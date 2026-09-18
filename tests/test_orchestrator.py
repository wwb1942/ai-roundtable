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
    assert orch.end_reason == "stalemate"

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

    assert orch.end_reason == "user_ended"
    assert orch.state == SessionState.COMPLETED
    p1.close_session.assert_called_once()
    p2.close_session.assert_called_once()


def test_finalize_for_workflow_preserves_stalemate_reason():
    p1 = make_fake_plugin("claude", "stalemate")
    p2 = make_fake_plugin("codex", "stalemate")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10, stalemate_threshold=1)
    orch.start()
    orch.run_auto()

    assert orch.state == SessionState.WAITING_FOR_USER
    orch.finalize_for_workflow(end_reason="stalemate")

    assert orch.state == SessionState.COMPLETED
    assert orch.end_reason == "stalemate"


def test_finalize_for_workflow_preserves_degraded_reason():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    p2.send_turn.return_value = ParticipantTurnResult(
        content="", raw_output="", status="unknown", status_summary=None,
        artifacts=[], error={"code": "timeout", "message": "timeout", "detail": None}, duration_ms=0,
        session_id=None, token_usage=None, cost_usd=None,
    )
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10, max_consecutive_failures=1)
    orch.start()
    orch.run_auto()

    assert orch.state == SessionState.WAITING_FOR_USER
    assert orch.end_reason == "degraded"
    orch.finalize_for_workflow(end_reason="fallback")

    assert orch.state == SessionState.COMPLETED
    assert orch.end_reason == "degraded"


def test_plugin_exception_becomes_structured_turn_failure():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    p1.send_turn.side_effect = RuntimeError("provider exploded")
    events = []
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1, on_event=events.append)

    orch.start()
    orch.run_auto()

    failed = [event for event in events if event["type"] == "turn_failed" and event["data"]["plugin_id"] == "claude"]
    assert len(failed) == 1
    assert failed[0]["data"]["error"]["code"] == "unknown"
    assert "provider exploded" in failed[0]["data"]["error"]["detail"]


def test_start_failure_closes_sessions_started_before_failure():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    p2.start_session.side_effect = RuntimeError("session unavailable")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)

    with pytest.raises(RuntimeError, match="session unavailable"):
        orch.start()

    assert orch.state == SessionState.FAILED
    p1.close_session.assert_called_once_with(None)
    p2.close_session.assert_not_called()


def test_finalization_closes_removed_participant_session():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    p2.send_turn.return_value = ParticipantTurnResult(
        content="", raw_output="", status="unknown", status_summary=None,
        artifacts=[], error={"code": "timeout", "message": "timeout", "detail": None},
        duration_ms=0, session_id=None, token_usage=None, cost_usd=None,
    )
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=2, max_consecutive_failures=1)

    orch.start()
    orch.run_auto()
    orch.finalize_degraded(mode="end")

    p1.close_session.assert_called_once()
    p2.close_session.assert_called_once()

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


def test_custom_contract_instruction_factory_and_working_directory_are_propagated():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    instruction_calls = []

    def instruction_factory(round_number, participant_id):
        instruction_calls.append((round_number, participant_id))
        return f"Round {round_number}: inspect as {participant_id}."

    orch = Orchestrator(
        topic="fix the issue",
        plugins=[p1, p2],
        max_rounds=1,
        system_contract="maintainer contract",
        instruction_factory=instruction_factory,
        working_directory="C:\\isolated-worktree",
    )

    orch.start()
    orch.run_auto()

    p1.start_session.assert_called_once_with("fix the issue", "maintainer contract")
    p2.start_session.assert_called_once_with("fix the issue", "maintainer contract")
    p1_context = p1.send_turn.call_args.args[1]
    p2_context = p2.send_turn.call_args.args[1]
    assert p1_context["system_contract"] == "maintainer contract"
    assert p2_context["system_contract"] == "maintainer contract"
    assert p1_context["turn_instruction"] == "Round 1: inspect as claude."
    assert p2_context["turn_instruction"] == "Round 1: inspect as codex."
    assert p1_context["working_directory"] == "C:\\isolated-worktree"
    assert p2_context["working_directory"] == "C:\\isolated-worktree"
    assert instruction_calls == [(1, "claude"), (1, "codex")]


def test_default_round_context_omits_working_directory_and_uses_standard_instruction():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)

    orch.start()
    orch.run_auto()

    context = p1.send_turn.call_args.args[1]
    assert context["turn_instruction"] == "Round 1. Discuss the topic and indicate your convergence status."
    assert "working_directory" not in context


def test_round_context_uses_one_snapshot_and_broadcasts_user_input():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    contexts = {}

    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)
    orch.start()
    orch._ctx.add_turn({"round_number": 0, "participant_id": "seed", "content": "seed"})
    orch.inject_user_input("shared constraint")

    def capture(plugin_id):
        def send(_session, context):
            contexts[plugin_id] = context
            if plugin_id == "claude":
                context["recent_turns"][0]["content"] = "participant mutation"
                context["user_inputs"].append("participant mutation")
            return make_fake_plugin(plugin_id, "diverging").send_turn.return_value

        return send

    p1.send_turn.side_effect = capture("claude")
    p2.send_turn.side_effect = capture("codex")
    orch.run_one_round()

    assert contexts["claude"]["round_number"] == 1
    assert contexts["codex"]["round_number"] == 1
    assert contexts["claude"]["user_inputs"] == ["shared constraint", "participant mutation"]
    assert contexts["codex"]["user_inputs"] == ["shared constraint"]
    assert contexts["codex"]["recent_turns"][0]["content"] == "seed"


def test_round_context_marks_only_mentioned_participant():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    contexts = {}
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)
    orch.start()
    # Use explicit side effects that return valid results after capturing.
    p1.send_turn.side_effect = lambda _session, context: (contexts.__setitem__("claude", context) or make_fake_plugin("claude").send_turn.return_value)
    p2.send_turn.side_effect = lambda _session, context: (contexts.__setitem__("codex", context) or make_fake_plugin("codex").send_turn.return_value)
    orch.inject_user_input("focus", mentions=["codex"])
    orch.run_one_round()

    assert contexts["claude"]["mentioned_by_user"] is False
    assert contexts["codex"]["mentioned_by_user"] is True


def test_successful_turn_keeps_status_summary_for_reports():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)

    orch.start()
    orch.run_one_round()

    assert all(turn["status_summary"] == "test" for turn in orch.turns)

def test_public_run_one_round_calls_plugins():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=2)
    orch.start()
    orch.run_one_round()
    assert orch.round_count == 1
    p1.send_turn.assert_called_once()


def test_public_run_one_round_finalizes_when_max_rounds_reached():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)
    orch.start()
    orch.run_one_round()
    orch.run_one_round()

    assert orch.state == SessionState.COMPLETED
    assert orch.end_reason == "max_rounds"
    assert p1.send_turn.call_count == 1

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


def test_failed_participant_prevents_false_convergence():
    p1 = make_fake_plugin("claude", "converging")
    p2 = make_fake_plugin("codex", "diverging")
    p2.send_turn.side_effect = OSError("temporary failure")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1, convergence_threshold=1)

    orch.start()
    orch.run_auto()

    assert orch.end_reason == "max_rounds"
    assert any(turn.get("error") for turn in orch.turns if turn["participant_id"] == "codex")


def test_partial_convergence_with_unknown_participant_does_not_finalize_as_consensus():
    plugins = [
        make_fake_plugin("a", "converging"),
        make_fake_plugin("b", "converging"),
        make_fake_plugin("c", "unknown"),
    ]
    orch = Orchestrator(topic="test", plugins=plugins, max_rounds=1, convergence_threshold=1)

    orch.start()
    orch.run_auto()

    assert orch.end_reason == "max_rounds"


def test_start_cannot_be_called_twice():
    orch = Orchestrator(topic="test", plugins=[make_fake_plugin("a"), make_fake_plugin("b")])
    orch.start()

    with pytest.raises(RuntimeError, match="only start from ready"):
        orch.start()


def test_error_blocks_heuristic_convergence():
    p1 = make_fake_plugin("a", "unknown")
    p2 = make_fake_plugin("b", "unknown")
    p3 = make_fake_plugin("c", "unknown")
    p1.send_turn.return_value["content"] = "Use a phased migration."
    p2.send_turn.return_value["content"] = "Use a phased migration."
    p3.send_turn.side_effect = RuntimeError("provider unavailable")
    orch = Orchestrator(topic="test", plugins=[p1, p2, p3], max_rounds=1, convergence_threshold=1)

    orch.start()
    orch.run_auto()

    assert orch.end_reason == "max_rounds"


def test_malformed_plugin_result_becomes_structured_failure():
    p1 = make_fake_plugin("claude", "diverging")
    p2 = make_fake_plugin("codex", "diverging")
    p1.send_turn.return_value = None
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)

    orch.start()
    orch.run_auto()

    assert any(
        turn.get("error", {}).get("message") == "participant send_turn raised an exception"
        for turn in orch.turns
        if turn["participant_id"] == "claude"
    )

def test_round_heuristic_receives_previous_and_two_current_responses():
    p1 = make_fake_plugin("claude", "unknown")
    p2 = make_fake_plugin("codex", "unknown")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)
    orch.start()
    with patch("src.orchestrator.heuristic_detect_many", return_value="unknown") as detector:
        orch.run_auto()

    detector.assert_called()
    previous, responses = detector.call_args.args
    assert len(previous) == 2
    assert responses == ["claude says something", "codex says something"]

def test_round_heuristic_uses_same_participant_previous_content():
    p1 = make_fake_plugin("claude", "unknown")
    p2 = make_fake_plugin("codex", "unknown")
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=1)
    orch._round_count = 2
    orch._all_turns = [
        {"round_number": 1, "participant_id": "codex", "sender": "Codex", "content": "previous codex"},
        {"round_number": 1, "participant_id": "claude", "sender": "Claude", "content": "previous claude"},
    ]
    with patch("src.orchestrator.heuristic_detect_many", return_value="unknown") as detector:
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
    previous, responses = detector.call_args.args
    assert previous == ["previous claude", "previous codex"]
    assert responses == ["current claude", "current codex"]
