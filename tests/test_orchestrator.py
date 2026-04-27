import pytest
from unittest.mock import MagicMock
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
    plugin.validate.return_value = None
    plugin.start_session.return_value = None
    plugin.send_turn.return_value = ParticipantTurnResult(
        content=f"{pid} says something", raw_output=f"{pid} raw",
        status=status, status_summary="test", artifacts=[], error=None, duration_ms=100,
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
        artifacts=[], error="timeout", duration_ms=0,
    )
    orch = Orchestrator(topic="test", plugins=[p1, p2], max_rounds=10, max_consecutive_failures=1)
    orch.start()
    orch.run_auto()
    assert orch.state == SessionState.DEGRADED

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
    )
    p2.send_turn.return_value = ParticipantTurnResult(
        content="me too", raw_output="", status="converging",
        status_summary="agreed", artifacts=[], error=None, duration_ms=100,
    )
    orch.inject_user_input("Consider team size")
    orch.resume_after_user()
    orch.run_auto()
    assert orch.state == SessionState.COMPLETED
