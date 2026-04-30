import pytest
from src.context_manager import ContextManager

def test_recent_window_within_limit():
    cm = ContextManager(window_size=3)
    turns = [{"round_number": i, "participant_id": "a", "content": f"msg{i}"} for i in range(3)]
    for t in turns:
        cm.add_turn(t)
    ctx = cm.build_context("topic", "Give your view.", speaker_id="claude")
    assert len(ctx["recent_turns"]) == 3
    assert ctx["history_summary"] == ""
    assert ctx["system_contract"] != ""

def test_system_contract_requires_status_block():
    cm = ContextManager(window_size=3)
    ctx = cm.build_context("topic", "Give your view.", speaker_id="claude")
    contract = ctx["system_contract"]
    assert "MUST end every response" in contract
    assert "<<<ROUNDTABLE_STATUS>>>" in contract
    assert '"status": "converging|diverging|stalemate"' in contract

def test_older_turns_become_summary():
    cm = ContextManager(window_size=2)
    for i in range(5):
        cm.add_turn({"round_number": i, "participant_id": f"ai{i%2}", "content": f"point {i}"})
    ctx = cm.build_context("topic", "Continue.", speaker_id="claude")
    assert len(ctx["recent_turns"]) == 2
    assert "[Roundtable Summary]" in ctx["history_summary"]
    assert "Topic: topic" in ctx["history_summary"]

def test_user_inputs_injected():
    cm = ContextManager(window_size=3)
    cm.add_user_input("我们团队只有3个人")
    ctx = cm.build_context("topic", "Consider this.", speaker_id="claude")
    assert "我们团队只有3个人" in ctx["user_inputs"]

def test_user_inputs_cleared_after_build():
    cm = ContextManager(window_size=3)
    cm.add_user_input("constraint")
    cm.build_context("topic", "Go.", speaker_id="claude")
    ctx2 = cm.build_context("topic", "Go again.", speaker_id="codex")
    assert ctx2["user_inputs"] == []

def test_round_number_increments():
    cm = ContextManager(window_size=3)
    ctx1 = cm.build_context("t", "go", speaker_id="claude")
    assert ctx1["round_number"] == 1
    cm.add_turn({"round_number": 1, "participant_id": "a", "content": "x"})
    ctx2 = cm.build_context("t", "go", speaker_id="codex")
    assert ctx2["round_number"] == 2
