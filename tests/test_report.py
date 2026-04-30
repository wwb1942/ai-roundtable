import json

from src.report import generate_report


def test_converged_report():
    md, js = generate_report(
        topic="Use Rust or Python",
        participants=["Claude", "Codex"],
        end_reason="converged",
        turns=[
            {"sender": "Claude", "content": "Python for prototyping"},
            {"sender": "Codex", "content": "Agreed, Python first"},
        ],
    )
    assert "# Roundtable Report" in md
    assert "Python" in md
    data = json.loads(js)
    assert data["end_reason"] == "converged"


def test_stalemate_report():
    md, js = generate_report(
        topic="Rewrite in Rust",
        participants=["Claude", "Codex"],
        end_reason="stalemate",
        turns=[{"sender": "Claude", "content": "disagree"}],
    )
    data = json.loads(js)
    assert data["end_reason"] == "stalemate"
    assert data["confidence"] == "low"


def test_degraded_report():
    md, js = generate_report(
        topic="test",
        participants=["Claude"],
        end_reason="degraded",
        turns=[],
    )
    assert "degraded" in md.lower() or "staged assessment" in md.lower()


def test_degraded_report_with_empty_turns_does_not_crash():
    md, js = generate_report(
        topic="test",
        participants=["Claude", "Codex"],
        end_reason="degraded",
        turns=[],
    )
    data = json.loads(js)
    assert data["end_reason"] == "degraded"
    assert "did not produce enough content" in data["final_conclusion"]


def test_json_structure():
    _, js = generate_report(
        topic="test", participants=["A", "B"], end_reason="max_rounds", turns=[],
    )
    data = json.loads(js)
    for key in [
        "topic",
        "participants",
        "end_reason",
        "final_conclusion",
        "recommendations",
        "risks",
        "open_questions",
        "disagreements",
        "confidence",
        "plan_ready",
        "plan_ready_reason",
    ]:
        assert key in data
