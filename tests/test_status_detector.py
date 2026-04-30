import pytest
from src.status_detector import extract_content_without_status_blocks, parse_status_block, heuristic_detect

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

def test_multiple_status_blocks_uses_last():
    text = '''First
<<<ROUNDTABLE_STATUS>>>
{"status": "diverging", "summary": "early"}
<<<END_STATUS>>>
Final
<<<ROUNDTABLE_STATUS>>>
{"status": "converging", "summary": "final"}
<<<END_STATUS>>>'''
    result = parse_status_block(text)
    assert result["status"] == "converging"
    assert result["warning"] == "multiple_status_blocks"
    assert result["status_block_count"] == 2

def test_extract_content_removes_all_status_blocks_but_keeps_analysis_between_them():
    text = '''First analysis
<<<ROUNDTABLE_STATUS>>>
{"status": "diverging", "summary": "early"}
<<<END_STATUS>>>
More analysis after first block
<<<ROUNDTABLE_STATUS>>>
{"status": "converging", "summary": "final"}
<<<END_STATUS>>>
Tail note'''
    content = extract_content_without_status_blocks(text)
    assert "First analysis" in content
    assert "More analysis after first block" in content
    assert "Tail note" in content
    assert "ROUNDTABLE_STATUS" not in content

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
