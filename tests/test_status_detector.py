import pytest
from src.status_detector import parse_status_block, heuristic_detect

def test_parse_valid_status_block():
    text = '''Here is my analysis...
<<<ROUNDTABLE_STATUS>>>
{"status": "converging", "summary": "Both agree on phased approach"}
<<<END_STATUS>>>'''
    result = parse_status_block(text)
    assert result is not None
    assert result["status"] == "converging"
    assert "phased approach" in result["summary"]

def test_parse_missing_block():
    assert parse_status_block("No status here") is None

def test_parse_malformed_json():
    text = '<<<ROUNDTABLE_STATUS>>>\n{bad json}\n<<<END_STATUS>>>'
    assert parse_status_block(text) is None

def test_ignores_json_outside_markers():
    text = '''{"status": "converging"} some text
<<<ROUNDTABLE_STATUS>>>
{"status": "diverging", "summary": "real one"}
<<<END_STATUS>>>'''
    result = parse_status_block(text)
    assert result["status"] == "diverging"

def test_heuristic_stalemate():
    prev = "I believe Python is better for rapid prototyping and team velocity."
    curr = "I still believe Python is better for rapid prototyping and team velocity."
    result = heuristic_detect(prev, curr, None, None)
    assert result in ("stalemate", "unknown")

def test_heuristic_converging():
    a_response = "We should use a phased migration approach."
    b_response = "Agreed, a phased migration makes the most sense."
    result = heuristic_detect(None, None, a_response, b_response)
    assert result in ("converging", "unknown")
