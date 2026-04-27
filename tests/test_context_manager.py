import pytest
from src.context_manager import ContextManager

def test_recent_window_within_limit():
    cm = ContextManager(window_size=3)
    turns = [{"round": i, "sender": "a", "content": f"msg{i}"} for i in range(3)]
    for t in turns:
        cm.add_turn(t)
    ctx = cm.build_context("topic", "Give your view.")
    assert len(ctx["recent_turns"]) == 3
    assert ctx["history_summary"] == ""

def test_older_turns_become_summary():
    cm = ContextManager(window_size=2)
    for i in range(5):
        cm.add_turn({"round": i, "sender": f"ai{i%2}", "content": f"point {i}"})
    ctx = cm.build_context("topic", "Continue.")
    assert len(ctx["recent_turns"]) == 2
    assert ctx["history_summary"] != ""

def test_user_inputs_injected():
    cm = ContextManager(window_size=3)
    cm.add_user_input("我们团队只有3个人")
    ctx = cm.build_context("topic", "Consider this.")
    assert "我们团队只有3个人" in ctx["user_inputs"]

def test_user_inputs_cleared_after_build():
    cm = ContextManager(window_size=3)
    cm.add_user_input("constraint")
    cm.build_context("topic", "Go.")
    ctx2 = cm.build_context("topic", "Go again.")
    assert ctx2["user_inputs"] == []

def test_round_number_increments():
    cm = ContextManager(window_size=3)
    ctx1 = cm.build_context("t", "go")
    assert ctx1["round_number"] == 1
    cm.add_turn({"round": 1, "sender": "a", "content": "x"})
    ctx2 = cm.build_context("t", "go")
    assert ctx2["round_number"] == 2
