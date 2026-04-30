import json
import tempfile
import os
import pytest
from src.event_log import EventLog

@pytest.fixture
def log_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d

def test_append_and_read(log_dir):
    log = EventLog(log_dir, "test-session")
    log.append("session_started", {"topic": "test"})
    events = log.read_all()
    assert len(events) == 1
    assert events[0]["type"] == "session_started"
    assert events[0]["schema_version"] == 1
    assert events[0]["data"]["topic"] == "test"

def test_read_after(log_dir):
    log = EventLog(log_dir, "test-session")
    log.append("round_started", {"round": 1})
    log.append("turn_completed", {"round": 1, "participant": "claude"})
    log.append("turn_completed", {"round": 1, "participant": "codex"})
    events = log.read_after(1)
    assert len(events) == 2

def test_empty_log(log_dir):
    log = EventLog(log_dir, "test-session")
    assert log.read_all() == []

def test_replay_returns_ordered(log_dir):
    log = EventLog(log_dir, "test-session")
    log.append("a", {})
    log.append("b", {})
    log.append("c", {})
    types = [e["type"] for e in log.read_all()]
    assert types == ["a", "b", "c"]

def test_file_persists(log_dir):
    log1 = EventLog(log_dir, "test-session")
    log1.append("session_started", {"topic": "persist"})
    log2 = EventLog(log_dir, "test-session")
    assert len(log2.read_all()) == 1

def test_rejects_path_traversal_session_id(log_dir):
    with pytest.raises(ValueError):
        EventLog(log_dir, "../escape")
