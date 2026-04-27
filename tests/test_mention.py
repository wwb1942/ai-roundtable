import pytest
from src.mention import parse_mentions

KNOWN = ["claude", "codex", "gemini"]

def test_extracts_known_mentions():
    assert parse_mentions("@claude what do you think?", KNOWN) == ["claude"]

def test_extracts_multiple_mentions():
    result = parse_mentions("@codex review this, @claude compare", KNOWN)
    assert result == ["codex", "claude"]

def test_deduplicates():
    assert parse_mentions("@claude hi @claude again", KNOWN) == ["claude"]

def test_ignores_unknown():
    assert parse_mentions("@bob hello", KNOWN) == []

def test_ignores_email():
    assert parse_mentions("send to user@claude.com", KNOWN) == []

def test_case_insensitive():
    assert parse_mentions("@Claude thoughts?", KNOWN) == ["claude"]

def test_empty_input():
    assert parse_mentions("", KNOWN) == []

def test_no_mentions():
    assert parse_mentions("just a normal message", KNOWN) == []
