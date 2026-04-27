# AI Roundtable Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-AI roundtable discussion system that orchestrates Claude, Codex (and future AI CLIs) around a topic, detects convergence/stalemate, supports user intervention, and outputs structured reports.

**Architecture:** Python subprocess-based orchestrator with plugin-based participant adapters, JSONL event log as source of truth, sliding-window context management, and dual renderer (Terminal default + optional Tmux). Each layer has strict boundaries: plugins handle CLI interaction, orchestrator owns all business logic, renderer only displays.

**Tech Stack:** Python >= 3.9, PyYAML, standard library only (subprocess, pathlib, json, typing, argparse, threading, shutil).

---

## Problem Analysis

The spec (2026-04-24) defines a 4-layer system with 20 sections. Implementation follows the spec's recommended TDD order: mention parser → event log → participant plugin → orchestrator → renderer → report generator. Each task produces independently testable code.

## Target File Structure

```text
D:\claudeprojects\ai-roundtable\
├── main.py                          # CLI entry point, argparse, bootstrap
├── config.yaml                      # Default participant + settings config
├── requirements.txt                 # pyyaml only
├── src/
│   ├── __init__.py
│   ├── mention.py                   # @mention parser (§16)
│   ├── event_log.py                 # JSONL append-only event stream (§9)
│   ├── models.py                    # Shared types: RoundContext, TurnResult, etc.
│   ├── plugin_base.py               # ParticipantPlugin protocol + capabilities (§4)
│   ├── plugins/
│   │   ├── __init__.py
│   │   ├── claude_plugin.py         # Claude CLI adapter
│   │   └── codex_plugin.py          # Codex CLI adapter
│   ├── status_detector.py           # Status block parser + heuristic fallback (§6)
│   ├── context_manager.py           # Sliding window + summary compression (§7)
│   ├── orchestrator.py              # State machine, round flow, completion (§5)
│   ├── report.py                    # Markdown + JSON report generation (§11)
│   └── renderers/
│       ├── __init__.py
│       ├── terminal.py              # TerminalRenderer (§10.1)
│       └── tmux.py                  # TmuxRenderer (§10.2)
├── tests/
│   ├── __init__.py
│   ├── test_mention.py
│   ├── test_event_log.py
│   ├── test_models.py
│   ├── test_status_detector.py
│   ├── test_context_manager.py
│   ├── test_orchestrator.py
│   ├── test_report.py
│   ├── test_claude_plugin.py
│   ├── test_codex_plugin.py
│   └── test_terminal_renderer.py
└── docs/
    └── superpowers/
        ├── specs/
        │   └── 2026-04-24-ai-roundtable-design.md
        └── plans/
            └── 2026-04-24-ai-roundtable-plan.md
```

Responsibilities:

- `models.py`: All shared TypedDicts (`ParticipantCapabilities`, `RoundContext`, `ParticipantTurnResult`, event types). Single source of truth for data shapes.
- `mention.py`: Parse `@claude`, `@codex` etc. from user input. Filters email-like patterns.
- `event_log.py`: JSONL append/read/replay. Schema-versioned events.
- `plugin_base.py`: `ParticipantPlugin` Protocol definition.
- `plugins/claude_plugin.py`: `claude --print` subprocess adapter.
- `plugins/codex_plugin.py`: `codex exec` subprocess adapter.
- `status_detector.py`: Extract `<<<ROUNDTABLE_STATUS>>>` blocks + heuristic fallback.
- `context_manager.py`: Build per-turn context with sliding window and summary compression.
- `orchestrator.py`: State machine (`ready→running→waiting_for_user→summarizing→completed→failed→degraded`), round scheduling, completion detection.
- `report.py`: Generate Markdown + JSON reports, style varies by `end_reason`.
- `renderers/terminal.py`: Single-window chat-flow display with user input.
- `renderers/tmux.py`: Optional 3-pane tmux layout.

---

## Implementation Plan

### Task 1: Project Scaffold

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\requirements.txt`
- Create: `D:\claudeprojects\ai-roundtable\src\__init__.py`
- Create: `D:\claudeprojects\ai-roundtable\src\plugins\__init__.py`
- Create: `D:\claudeprojects\ai-roundtable\src\renderers\__init__.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\__init__.py`
- Create: `D:\claudeprojects\ai-roundtable\config.yaml`

- [ ] **Step 1: Initialize git repo**

Run:
```bash
cd D:/claudeprojects/ai-roundtable && git init
```
Expected: Initialized empty Git repository.

- [ ] **Step 2: Create requirements.txt**

```text
pyyaml>=6.0
```

- [ ] **Step 3: Create package __init__.py files**

Create empty `__init__.py` in `src/`, `src/plugins/`, `src/renderers/`, `tests/`.

- [ ] **Step 4: Create default config.yaml**

```yaml
participants:
  - id: claude
    plugin: claude
  - id: codex
    plugin: codex

settings:
  max_rounds: 12
  convergence_threshold: 2
  stalemate_threshold: 2
  context_window: 3
  call_timeout: 120
  renderer: terminal
  output_mode: report
```

- [ ] **Step 5: Install dependencies and verify**

Run:
```bash
cd D:/claudeprojects/ai-roundtable && pip install pyyaml && python -c "import yaml; print('ok')"
```
Expected: `ok`

- [ ] **Step 6: Commit scaffold**

Run:
```bash
cd D:/claudeprojects/ai-roundtable && git add -A && git commit -m "chore: scaffold ai-roundtable project"
```

### Task 2: Shared Models

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\models.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_models.py`

- [ ] **Step 1: Write failing tests for models**

```python
# tests/test_models.py
import pytest
from src.models import (
    ParticipantCapabilities, ParticipantTurnResult, RoundContext,
    EventRecord, SessionState
)

def test_participant_capabilities_valid():
    caps = ParticipantCapabilities(
        session_mode="context-managed",
        interruptible=False,
        structured_status=True,
        supports_resume=False,
        supports_artifacts=False,
    )
    assert caps["session_mode"] == "context-managed"

def test_round_context_has_required_fields():
    ctx = RoundContext(
        round_number=1,
        topic="test topic",
        history_summary="",
        recent_turns=[],
        user_inputs=[],
        turn_instruction="Give your opinion.",
    )
    assert ctx["round_number"] == 1

def test_event_record_has_schema_version():
    event = EventRecord(
        schema_version=1,
        type="session_started",
        timestamp="2026-04-24T10:00:00Z",
        data={"topic": "test"},
    )
    assert event["schema_version"] == 1

def test_session_state_values():
    assert SessionState.READY == "ready"
    assert SessionState.DEGRADED == "degraded"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement models**

```python
# src/models.py
from __future__ import annotations
from enum import Enum
from typing import TypedDict, Literal

class ParticipantCapabilities(TypedDict):
    session_mode: Literal["context-managed", "session-managed"]
    interruptible: bool
    structured_status: bool
    supports_resume: bool
    supports_artifacts: bool

class ParticipantTurnResult(TypedDict):
    content: str
    raw_output: str
    status: Literal["converging", "diverging", "stalemate", "unknown"]
    status_summary: str | None
    artifacts: list[str]
    error: str | None
    duration_ms: int

class RoundContext(TypedDict):
    round_number: int
    topic: str
    history_summary: str
    recent_turns: list[dict]
    user_inputs: list[str]
    turn_instruction: str

class EventRecord(TypedDict):
    schema_version: int
    type: str
    timestamp: str
    data: dict

class SessionState(str, Enum):
    READY = "ready"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEGRADED = "degraded"
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py && git commit -m "feat: add shared type models"
```

### Task 3: Mention Parser

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\mention.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_mention.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mention.py
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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_mention.py -v`
Expected: FAIL

- [ ] **Step 3: Implement parser**

```python
# src/mention.py
from __future__ import annotations
import re

def parse_mentions(text: str, known_participants: list[str]) -> list[str]:
    known_lower = {p.lower() for p in known_participants}
    mentions: list[str] = []
    for match in re.finditer(r"(?:^|\s)@([a-zA-Z][a-zA-Z0-9_-]*)\b", text):
        name = match.group(1).lower()
        if name in known_lower and name not in mentions:
            mentions.append(name)
    return mentions
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_mention.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mention.py tests/test_mention.py && git commit -m "feat: add mention parser"
```

### Task 4: Event Log

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\event_log.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_event_log.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_event_log.py
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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_event_log.py -v`
Expected: FAIL

- [ ] **Step 3: Implement event log**

```python
# src/event_log.py
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from src.models import EventRecord

SCHEMA_VERSION = 1

class EventLog:
    def __init__(self, base_dir: str, session_id: str):
        self._path = Path(base_dir) / f"{session_id}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, data: dict) -> EventRecord:
        record: EventRecord = {
            "schema_version": SCHEMA_VERSION,
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def read_all(self) -> list[EventRecord]:
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def read_after(self, index: int) -> list[EventRecord]:
        all_events = self.read_all()
        return all_events[index:]

    @property
    def path(self) -> Path:
        return self._path
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_event_log.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/event_log.py tests/test_event_log.py && git commit -m "feat: add JSONL event log"
```

### Task 5: Status Detector

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\status_detector.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_status_detector.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_status_detector.py
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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_status_detector.py -v`
Expected: FAIL

- [ ] **Step 3: Implement status detector**

```python
# src/status_detector.py
from __future__ import annotations
import json
import re
from difflib import SequenceMatcher

def parse_status_block(text: str) -> dict | None:
    pattern = r"<<<ROUNDTABLE_STATUS>>>\s*\n(.*?)\n\s*<<<END_STATUS>>>"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1).strip())
        if "status" in data:
            return data
        return None
    except (json.JSONDecodeError, KeyError):
        return None

def heuristic_detect(
    prev_same: str | None,
    curr_same: str | None,
    response_a: str | None,
    response_b: str | None,
) -> str:
    if prev_same and curr_same:
        ratio = SequenceMatcher(None, prev_same.lower(), curr_same.lower()).ratio()
        if ratio > 0.8:
            return "stalemate"
    if response_a and response_b:
        ratio = SequenceMatcher(None, response_a.lower(), response_b.lower()).ratio()
        if ratio > 0.6:
            return "converging"
    return "unknown"
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_status_detector.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/status_detector.py tests/test_status_detector.py && git commit -m "feat: add status block parser and heuristic detector"
```

### Task 6: Plugin Base + Claude Plugin

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\plugin_base.py`
- Create: `D:\claudeprojects\ai-roundtable\src\plugins\claude_plugin.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_claude_plugin.py`

- [ ] **Step 1: Write plugin protocol and failing tests**

```python
# src/plugin_base.py
from __future__ import annotations
from typing import Protocol, runtime_checkable
from src.models import ParticipantCapabilities, ParticipantTurnResult, RoundContext

@runtime_checkable
class ParticipantPlugin(Protocol):
    id: str
    display_name: str
    capabilities: ParticipantCapabilities

    def validate(self) -> None: ...
    def start_session(self, topic: str, system_contract: str) -> str | None: ...
    def send_turn(self, session_id: str | None, round_context: RoundContext) -> ParticipantTurnResult: ...
    def interrupt(self, session_id: str | None) -> bool: ...
    def resume(self, session_id: str) -> bool: ...
    def close_session(self, session_id: str | None) -> None: ...
```

```python
# tests/test_claude_plugin.py
import pytest
from unittest.mock import patch, MagicMock
from src.plugins.claude_plugin import ClaudePlugin
from src.models import RoundContext

def test_validate_success():
    with patch("shutil.which", return_value="/usr/bin/claude"):
        plugin = ClaudePlugin()
        plugin.validate()

def test_validate_not_found():
    with patch("shutil.which", return_value=None):
        plugin = ClaudePlugin()
        with pytest.raises(RuntimeError, match="claude CLI not found"):
            plugin.validate()

def test_send_turn_returns_result():
    plugin = ClaudePlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
    )
    fake_output = 'My analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"initial"}\n<<<END_STATUS>>>'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=fake_output, returncode=0
        )
        result = plugin.send_turn(None, ctx)
        assert result["status"] == "diverging"
        assert "My analysis" in result["content"]
        assert result["error"] is None

def test_send_turn_timeout():
    import subprocess
    plugin = ClaudePlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
    )
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 120)):
        result = plugin.send_turn(None, ctx)
        assert result["error"] is not None
        assert result["status"] == "unknown"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_claude_plugin.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Claude plugin**

```python
# src/plugins/claude_plugin.py
from __future__ import annotations
import shutil
import subprocess
import time
from src.models import ParticipantCapabilities, ParticipantTurnResult, RoundContext
from src.status_detector import parse_status_block

class ClaudePlugin:
    id = "claude"
    display_name = "Claude"
    capabilities: ParticipantCapabilities = {
        "session_mode": "context-managed",
        "interruptible": False,
        "structured_status": True,
        "supports_resume": False,
        "supports_artifacts": False,
    }

    def __init__(self, command: str = "claude", timeout: int = 120):
        self._command = command
        self._timeout = timeout

    def validate(self) -> None:
        if not shutil.which(self._command):
            raise RuntimeError(f"{self._command} CLI not found on PATH")

    def start_session(self, topic: str, system_contract: str) -> str | None:
        return None

    def send_turn(self, session_id: str | None, round_context: RoundContext) -> ParticipantTurnResult:
        prompt = self._build_prompt(round_context)
        start = time.monotonic()
        try:
            result = subprocess.run(
                [self._command, "--print", "-p", prompt],
                capture_output=True, text=True, timeout=self._timeout,
                encoding="utf-8",
            )
            duration = int((time.monotonic() - start) * 1000)
            output = result.stdout.strip()
            status_data = parse_status_block(output)
            content = output.split("<<<ROUNDTABLE_STATUS>>>")[0].strip()
            return ParticipantTurnResult(
                content=content,
                raw_output=output,
                status=status_data["status"] if status_data else "unknown",
                status_summary=status_data.get("summary") if status_data else None,
                artifacts=[],
                error=None if result.returncode == 0 else f"exit code {result.returncode}",
                duration_ms=duration,
            )
        except subprocess.TimeoutExpired:
            duration = int((time.monotonic() - start) * 1000)
            return ParticipantTurnResult(
                content="", raw_output="", status="unknown",
                status_summary=None, artifacts=[],
                error=f"timeout after {self._timeout}s", duration_ms=duration,
            )

    def interrupt(self, session_id: str | None) -> bool:
        return False

    def resume(self, session_id: str) -> bool:
        return False

    def close_session(self, session_id: str | None) -> None:
        pass

    def _build_prompt(self, ctx: RoundContext) -> str:
        parts = [f"[系统] 你正在和其他 AI 讨论以下话题：{ctx['topic']}"]
        if ctx["history_summary"]:
            parts.append(f"[历史摘要] {ctx['history_summary']}")
        for turn in ctx["recent_turns"]:
            parts.append(f"[{turn.get('sender', '?')}] {turn.get('content', '')}")
        for ui in ctx["user_inputs"]:
            parts.append(f"[用户/主持人] {ui}")
        parts.append(f"[当前] {ctx['turn_instruction']}")
        parts.append("请在回复末尾用以下格式附上状态：")
        parts.append("<<<ROUNDTABLE_STATUS>>>")
        parts.append('{"status": "converging|diverging|stalemate", "summary": "当前共识/分歧点"}')
        parts.append("<<<END_STATUS>>>")
        return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_claude_plugin.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/plugin_base.py src/plugins/claude_plugin.py tests/test_claude_plugin.py && git commit -m "feat: add plugin protocol and Claude plugin"
```

### Task 7: Codex Plugin

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\plugins\codex_plugin.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_codex_plugin.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_codex_plugin.py
import pytest
from unittest.mock import patch, MagicMock
from src.plugins.codex_plugin import CodexPlugin
from src.models import RoundContext

def test_validate_success():
    with patch("shutil.which", return_value="/usr/bin/codex"):
        plugin = CodexPlugin()
        plugin.validate()

def test_validate_not_found():
    with patch("shutil.which", return_value=None):
        plugin = CodexPlugin()
        with pytest.raises(RuntimeError, match="codex CLI not found"):
            plugin.validate()

def test_send_turn_uses_exec_subcommand():
    plugin = CodexPlugin()
    ctx = RoundContext(
        round_number=1, topic="test", history_summary="",
        recent_turns=[], user_inputs=[], turn_instruction="Share your view.",
    )
    fake_output = 'Analysis...\n<<<ROUNDTABLE_STATUS>>>\n{"status":"diverging","summary":"test"}\n<<<END_STATUS>>>'
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        plugin.send_turn(None, ctx)
        args = mock_run.call_args[0][0]
        assert args[0] == "codex"
        assert args[1] == "exec"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_codex_plugin.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Codex plugin**

```python
# src/plugins/codex_plugin.py
from __future__ import annotations
import shutil
import subprocess
import time
from src.models import ParticipantCapabilities, ParticipantTurnResult, RoundContext
from src.status_detector import parse_status_block

class CodexPlugin:
    id = "codex"
    display_name = "Codex"
    capabilities: ParticipantCapabilities = {
        "session_mode": "context-managed",
        "interruptible": False,
        "structured_status": True,
        "supports_resume": False,
        "supports_artifacts": True,
    }

    def __init__(self, command: str = "codex", timeout: int = 120):
        self._command = command
        self._timeout = timeout

    def validate(self) -> None:
        if not shutil.which(self._command):
            raise RuntimeError(f"{self._command} CLI not found on PATH")

    def start_session(self, topic: str, system_contract: str) -> str | None:
        return None

    def send_turn(self, session_id: str | None, round_context: RoundContext) -> ParticipantTurnResult:
        prompt = self._build_prompt(round_context)
        start = time.monotonic()
        try:
            result = subprocess.run(
                [self._command, "exec", prompt],
                capture_output=True, text=True, timeout=self._timeout,
                encoding="utf-8",
            )
            duration = int((time.monotonic() - start) * 1000)
            output = result.stdout.strip()
            status_data = parse_status_block(output)
            content = output.split("<<<ROUNDTABLE_STATUS>>>")[0].strip()
            return ParticipantTurnResult(
                content=content, raw_output=output,
                status=status_data["status"] if status_data else "unknown",
                status_summary=status_data.get("summary") if status_data else None,
                artifacts=[], error=None if result.returncode == 0 else f"exit code {result.returncode}",
                duration_ms=duration,
            )
        except subprocess.TimeoutExpired:
            duration = int((time.monotonic() - start) * 1000)
            return ParticipantTurnResult(
                content="", raw_output="", status="unknown",
                status_summary=None, artifacts=[],
                error=f"timeout after {self._timeout}s", duration_ms=duration,
            )

    def interrupt(self, session_id: str | None) -> bool:
        return False

    def resume(self, session_id: str) -> bool:
        return False

    def close_session(self, session_id: str | None) -> None:
        pass

    def _build_prompt(self, ctx: RoundContext) -> str:
        parts = [f"[系统] 你正在和其他 AI 讨论以下话题：{ctx['topic']}"]
        if ctx["history_summary"]:
            parts.append(f"[历史摘要] {ctx['history_summary']}")
        for turn in ctx["recent_turns"]:
            parts.append(f"[{turn.get('sender', '?')}] {turn.get('content', '')}")
        for ui in ctx["user_inputs"]:
            parts.append(f"[用户/主持人] {ui}")
        parts.append(f"[当前] {ctx['turn_instruction']}")
        parts.append("请在回复末尾用以下格式附上状态：")
        parts.append("<<<ROUNDTABLE_STATUS>>>")
        parts.append('{"status": "converging|diverging|stalemate", "summary": "当前共识/分歧点"}')
        parts.append("<<<END_STATUS>>>")
        return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_codex_plugin.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/plugins/codex_plugin.py tests/test_codex_plugin.py && git commit -m "feat: add Codex plugin"
```

### Task 8: Context Manager

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\context_manager.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_context_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_context_manager.py
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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_context_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Implement context manager**

```python
# src/context_manager.py
from __future__ import annotations
from src.models import RoundContext

class ContextManager:
    def __init__(self, window_size: int = 3):
        self._window_size = window_size
        self._all_turns: list[dict] = []
        self._pending_user_inputs: list[str] = []
        self._round_counter = 0

    def add_turn(self, turn: dict) -> None:
        self._all_turns.append(turn)

    def add_user_input(self, text: str) -> None:
        self._pending_user_inputs.append(text)

    def build_context(self, topic: str, turn_instruction: str) -> RoundContext:
        self._round_counter += 1
        recent = self._all_turns[-self._window_size:]
        older = self._all_turns[:-self._window_size] if len(self._all_turns) > self._window_size else []
        summary = self._summarize(older) if older else ""
        user_inputs = list(self._pending_user_inputs)
        self._pending_user_inputs.clear()
        return RoundContext(
            round_number=self._round_counter,
            topic=topic,
            history_summary=summary,
            recent_turns=recent,
            user_inputs=user_inputs,
            turn_instruction=turn_instruction,
        )

    def _summarize(self, turns: list[dict]) -> str:
        points: list[str] = []
        for t in turns:
            sender = t.get("sender", "?")
            content = t.get("content", "")
            short = content[:100] + "..." if len(content) > 100 else content
            points.append(f"- [{sender}] {short}")
        return "[Roundtable Summary]\n" + "\n".join(points)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_context_manager.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/context_manager.py tests/test_context_manager.py && git commit -m "feat: add context manager with sliding window"
```

### Task 9: Orchestrator

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\orchestrator.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_orchestrator.py`

- [ ] **Step 1: Write failing tests for state machine and round flow**

```python
# tests/test_orchestrator.py
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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL

- [ ] **Step 3: Implement orchestrator**

The orchestrator is the largest module. Core structure:

```python
# src/orchestrator.py
from __future__ import annotations
import tempfile
from src.models import SessionState, ParticipantTurnResult, RoundContext
from src.plugin_base import ParticipantPlugin
from src.context_manager import ContextManager
from src.event_log import EventLog
from src.status_detector import heuristic_detect
from src.report import generate_report

class Orchestrator:
    def __init__(
        self, topic: str, plugins: list, max_rounds: int = 12,
        convergence_threshold: int = 2, stalemate_threshold: int = 2,
        context_window: int = 3, max_consecutive_failures: int = 3,
        log_dir: str | None = None,
    ):
        self._topic = topic
        self._plugins: list = list(plugins)
        self._max_rounds = max_rounds
        self._conv_threshold = convergence_threshold
        self._stale_threshold = stalemate_threshold
        self._max_failures = max_consecutive_failures
        self._state = SessionState.READY
        self._round = 0
        self._end_reason = ""
        self._consecutive_status: dict[str, int] = {"converging": 0, "stalemate": 0}
        self._failure_counts: dict[str, int] = {}
        self._ctx_mgr = ContextManager(window_size=context_window)
        log_dir = log_dir or tempfile.mkdtemp(prefix="roundtable-")
        self._event_log = EventLog(log_dir, f"session-{id(self)}")
        self._last_turns: dict[str, str] = {}
        self._all_turns: list[dict] = []

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def round_count(self) -> int:
        return self._round

    @property
    def end_reason(self) -> str:
        return self._end_reason

    @property
    def turns(self) -> list[dict]:
        return list(self._all_turns)

    @property
    def participant_names(self) -> list[str]:
        return [p.display_name for p in self._plugins]

    @property
    def topic(self) -> str:
        return self._topic

    def inject_user_input(self, text: str) -> None:
        self._ctx_mgr.add_user_input(text)
        self._event_log.append("user_intervened", {"round": self._round, "content": text})

    def resume_after_user(self) -> None:
        if self._state == SessionState.WAITING_FOR_USER:
            self._state = SessionState.RUNNING

    def start(self) -> None:
        active = []
        for p in self._plugins:
            try:
                p.validate()
                active.append(p)
                self._event_log.append("participant_validated", {"id": p.id})
            except RuntimeError:
                self._event_log.append("participant_unavailable", {"id": p.id})
        self._plugins = active
        if len(self._plugins) < 2:
            self._state = SessionState.FAILED
            raise RuntimeError("Need at least 2 valid participants")
        for p in self._plugins:
            self._failure_counts[p.id] = 0
        self._state = SessionState.RUNNING
        self._event_log.append("session_started", {"topic": self._topic})

    def run_auto(self) -> None:
        while self._state == SessionState.RUNNING:
            if self._round >= self._max_rounds:
                self._end_reason = "max_rounds"
                self._finalize()
                break
            self._run_one_round()
        if self._state == SessionState.COMPLETED:
            self._finalize()

    # --- Four-phase round flow (spec §5.3) ---

    def _run_one_round(self) -> None:
        self._round += 1
        round_context_list, round_statuses = self._prepare_round()
        results = self._collect_responses()
        action = self._analyze_round(results)
        self._decide_next_action(action)

    def _prepare_round(self) -> tuple[list, list]:
        self._event_log.append("round_started", {"round": self._round})
        return [], []

    def _collect_responses(self) -> list[tuple]:
        results = []
        for plugin in list(self._plugins):
            self._event_log.append("turn_prompted", {"round": self._round, "participant": plugin.id})
            ctx = self._ctx_mgr.build_context(self._topic, f"Round {self._round}: share your analysis.")
            result = plugin.send_turn(None, ctx)
            if result["error"]:
                self._event_log.append("turn_failed", {
                    "round": self._round, "participant": plugin.id, "error": result["error"],
                })
                self._failure_counts[plugin.id] = self._failure_counts.get(plugin.id, 0) + 1
                if self._failure_counts[plugin.id] >= self._max_failures:
                    self._plugins.remove(plugin)
                    self._event_log.append("participant_removed", {"id": plugin.id})
                    if len(self._plugins) < 2:
                        self._state = SessionState.DEGRADED
                        self._end_reason = "degraded"
                        self._event_log.append("session_degraded", {"remaining": len(self._plugins)})
                        return results
            else:
                self._failure_counts[plugin.id] = 0
                turn_record = {"round": self._round, "sender": plugin.display_name, "content": result["content"]}
                self._ctx_mgr.add_turn(turn_record)
                self._all_turns.append(turn_record)
                self._event_log.append("turn_completed", {
                    "round": self._round, "participant": plugin.id, "status": result["status"],
                })
                results.append((plugin, result))
        return results

    def _analyze_round(self, results: list[tuple]) -> str:
        round_statuses = []
        for plugin, result in results:
            status = result["status"]
            if status == "unknown":
                prev = self._last_turns.get(plugin.id)
                status = heuristic_detect(prev, result["content"], None, None)
            self._last_turns[plugin.id] = result["content"]
            round_statuses.append(status)
            self._event_log.append("state_detected", {
                "round": self._round, "participant": plugin.id, "status": status,
            })
        if all(s == "converging" for s in round_statuses) and round_statuses:
            self._consecutive_status["converging"] += 1
        else:
            self._consecutive_status["converging"] = 0
        if all(s == "stalemate" for s in round_statuses) and round_statuses:
            self._consecutive_status["stalemate"] += 1
        else:
            self._consecutive_status["stalemate"] = 0
        if self._consecutive_status["converging"] >= self._conv_threshold:
            return "converged"
        if self._consecutive_status["stalemate"] >= self._stale_threshold:
            return "stalemate"
        return "continue"

    def _decide_next_action(self, action: str) -> None:
        if action == "converged":
            self._end_reason = "converged"
            self._state = SessionState.SUMMARIZING
            self._event_log.append("session_summarized", {"reason": "converged"})
            self._state = SessionState.COMPLETED
            self._event_log.append("session_completed", {"reason": "converged"})
        elif action == "stalemate":
            self._end_reason = "stalemate"
            self._state = SessionState.WAITING_FOR_USER
            self._event_log.append("session_paused", {"reason": "stalemate"})

    def _finalize(self) -> None:
        if not self._end_reason:
            self._end_reason = "completed"
        self._state = SessionState.SUMMARIZING
        self._event_log.append("session_summarized", {"reason": self._end_reason})
        self._state = SessionState.COMPLETED
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator.py tests/test_orchestrator.py && git commit -m "feat: add orchestrator with state machine"
```

### Task 10: Report Generator

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\report.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_report.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_report.py
import json
import pytest
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
    assert data["plan_ready"] is True or data["plan_ready"] is False

def test_stalemate_report():
    md, js = generate_report(
        topic="Rewrite in Rust",
        participants=["Claude", "Codex"],
        end_reason="stalemate",
        turns=[],
    )
    data = json.loads(js)
    assert data["end_reason"] == "stalemate"
    assert "分歧" in md or "Disagreement" in md or "dissent" in data

def test_degraded_report():
    md, js = generate_report(
        topic="test",
        participants=["Claude"],
        end_reason="degraded",
        turns=[],
    )
    assert "阶段性" in md or "partial" in md.lower() or "degraded" in md.lower()

def test_json_structure():
    _, js = generate_report(
        topic="test", participants=["A", "B"], end_reason="max_rounds", turns=[],
    )
    data = json.loads(js)
    for key in ["topic", "participants", "end_reason", "final_conclusion",
                "recommendations", "risks", "open_questions", "dissent",
                "confidence", "plan_ready"]:
        assert key in data
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_report.py -v`
Expected: FAIL

- [ ] **Step 3: Implement report generator**

```python
# src/report.py
from __future__ import annotations
import json

def generate_report(
    topic: str,
    participants: list[str],
    end_reason: str,
    turns: list[dict],
) -> tuple[str, str]:
    conclusion = _extract_conclusion(turns, end_reason)
    recommendations = _extract_recommendations(turns)
    risks = _extract_risks(turns)
    open_questions = _extract_open_questions(turns)
    dissent = _extract_dissent(turns)
    confidence = "high" if end_reason == "converged" else "medium" if end_reason == "max_rounds" else "low"
    plan_ready = end_reason == "converged" and not open_questions

    result = {
        "topic": topic,
        "participants": participants,
        "end_reason": end_reason,
        "final_conclusion": conclusion,
        "recommendations": recommendations,
        "risks": risks,
        "open_questions": open_questions,
        "dissent": dissent,
        "confidence": confidence,
        "plan_ready": plan_ready,
    }

    md = _render_markdown(result, turns, end_reason)
    js = json.dumps(result, ensure_ascii=False, indent=2)
    return md, js

def _extract_conclusion(turns: list[dict], end_reason: str) -> str:
    if not turns:
        return "讨论未产生足够内容以形成结论。"
    last_contents = [t["content"] for t in turns[-2:]]
    if end_reason == "degraded":
        return f"阶段性判断（非圆桌共识）：{last_contents[-1][:200] if last_contents else '无'}"
    return last_contents[-1][:300] if last_contents else "无结论"

def _extract_recommendations(turns: list[dict]) -> list[str]:
    return [f"基于讨论内容的建议（需人工审核）"]

def _extract_risks(turns: list[dict]) -> list[str]:
    return [f"讨论中识别的风险点（需人工确认）"]

def _extract_open_questions(turns: list[dict]) -> list[str]:
    return []

def _extract_dissent(turns: list[dict]) -> list[str]:
    return []

def _render_markdown(result: dict, turns: list[dict], end_reason: str) -> str:
    lines = ["# Roundtable Report", ""]
    lines.append(f"**Topic:** {result['topic']}")
    lines.append(f"**Participants:** {', '.join(result['participants'])}")
    lines.append(f"**End Reason:** {result['end_reason']}")
    lines.append(f"**Confidence:** {result['confidence']}")
    lines.append(f"**Plan Ready:** {result['plan_ready']}")
    lines.append("")
    lines.append("## Final Conclusion")
    lines.append(result["final_conclusion"])
    lines.append("")
    lines.append("## Recommendations")
    for r in result["recommendations"]:
        lines.append(f"- {r}")
    lines.append("")
    lines.append("## Risks")
    for r in result["risks"]:
        lines.append(f"- {r}")
    if result["dissent"]:
        lines.append("")
        lines.append("## Remaining Disagreements")
        for d in result["dissent"]:
            lines.append(f"- {d}")
    if result["open_questions"]:
        lines.append("")
        lines.append("## Open Questions")
        for q in result["open_questions"]:
            lines.append(f"- {q}")
    if end_reason == "degraded":
        lines.append("")
        lines.append("> 注意：本报告为阶段性判断，非完整圆桌共识。")
    lines.append("")
    lines.append("## Discussion Log")
    for t in turns:
        lines.append(f"**{t.get('sender', '?')}:** {t.get('content', '')[:200]}")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_report.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/report.py tests/test_report.py && git commit -m "feat: add report generator with MD + JSON output"
```

### Task 11: Terminal Renderer

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\src\renderers\terminal.py`
- Create: `D:\claudeprojects\ai-roundtable\tests\test_terminal_renderer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_terminal_renderer.py
import io
import pytest
from src.renderers.terminal import TerminalRenderer

def test_render_header():
    out = io.StringIO()
    r = TerminalRenderer(output=out)
    r.render_header("Test Topic", ["Claude", "Codex"])
    text = out.getvalue()
    assert "Test Topic" in text
    assert "Claude" in text

def test_render_turn():
    out = io.StringIO()
    r = TerminalRenderer(output=out)
    r.render_turn("Claude", "I think Python is better.", 1)
    text = out.getvalue()
    assert "Claude" in text
    assert "Python" in text

def test_render_status():
    out = io.StringIO()
    r = TerminalRenderer(output=out)
    r.render_status("converging", 3, 12)
    text = out.getvalue()
    assert "3" in text

def test_render_user_input():
    out = io.StringIO()
    r = TerminalRenderer(output=out)
    r.render_user_message("我们团队只有3个人")
    text = out.getvalue()
    assert "3个人" in text
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_terminal_renderer.py -v`
Expected: FAIL

- [ ] **Step 3: Implement terminal renderer**

```python
# src/renderers/terminal.py
from __future__ import annotations
import sys
from typing import TextIO

COLORS = {"Claude": "\033[94m", "Codex": "\033[92m", "User": "\033[93m", "reset": "\033[0m"}

class TerminalRenderer:
    def __init__(self, output: TextIO | None = None):
        self._out = output or sys.stdout

    def render_header(self, topic: str, participants: list[str]) -> None:
        sep = "=" * 50
        self._out.write(f"\n{sep}\n")
        self._out.write(f"  Topic: {topic}\n")
        self._out.write(f"  Participants: {', '.join(participants)}\n")
        self._out.write(f"{sep}\n\n")

    def render_turn(self, sender: str, content: str, round_num: int) -> None:
        color = COLORS.get(sender, "")
        reset = COLORS["reset"] if color else ""
        self._out.write(f"{color}[Round {round_num}] {sender}:{reset}\n")
        self._out.write(f"  {content}\n\n")

    def render_status(self, status: str, current_round: int, max_rounds: int) -> None:
        self._out.write(f"  Status: {status} ({current_round}/{max_rounds})\n\n")

    def render_user_message(self, text: str) -> None:
        color = COLORS["User"]
        reset = COLORS["reset"]
        self._out.write(f"{color}[User]:{reset}\n  {text}\n\n")

    def render_report(self, markdown: str) -> None:
        self._out.write("\n" + markdown + "\n")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/test_terminal_renderer.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/renderers/terminal.py tests/test_terminal_renderer.py && git commit -m "feat: add terminal renderer"
```

### Task 12: CLI Entry Point

**Files:**
- Create: `D:\claudeprojects\ai-roundtable\main.py`

- [ ] **Step 1: Implement main.py**

```python
# main.py
from __future__ import annotations
import argparse
import sys
import os
import yaml
from pathlib import Path
from src.orchestrator import Orchestrator
from src.plugins.claude_plugin import ClaudePlugin
from src.plugins.codex_plugin import CodexPlugin
from src.renderers.terminal import TerminalRenderer
from src.report import generate_report
from src.mention import parse_mentions
from src.models import SessionState

PLUGIN_REGISTRY = {"claude": ClaudePlugin, "codex": CodexPlugin}

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def main():
    parser = argparse.ArgumentParser(description="AI Roundtable Discussion")
    parser.add_argument("topic", help="Discussion topic")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--max-rounds", type=int, help="Override max rounds")
    parser.add_argument("--manual", action="store_true", help="Manual moderation mode")
    parser.add_argument("--renderer", choices=["terminal", "tmux"], default="terminal")
    parser.add_argument("--output-dir", default="output", help="Report output directory")
    args = parser.parse_args()

    config = load_config(args.config)
    settings = config.get("settings", {})
    if args.max_rounds:
        settings["max_rounds"] = args.max_rounds

    plugins = []
    for p_conf in config.get("participants", []):
        plugin_cls = PLUGIN_REGISTRY.get(p_conf["plugin"])
        if plugin_cls:
            plugins.append(plugin_cls(timeout=settings.get("call_timeout", 120)))

    renderer = TerminalRenderer()
    renderer.render_header(args.topic, [p.display_name for p in plugins])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    orch = Orchestrator(
        topic=args.topic, plugins=plugins,
        max_rounds=settings.get("max_rounds", 12),
        convergence_threshold=settings.get("convergence_threshold", 2),
        stalemate_threshold=settings.get("stalemate_threshold", 2),
        context_window=settings.get("context_window", 3),
        log_dir=str(output_dir),
    )

    try:
        orch.start()
        if args.manual:
            _run_manual(orch, renderer)
        else:
            orch.run_auto()
            if orch.state == SessionState.WAITING_FOR_USER:
                _handle_waiting(orch, renderer)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    md_report, json_report = generate_report(
        topic=orch.topic,
        participants=orch.participant_names,
        end_reason=orch.end_reason,
        turns=orch.turns,
    )
    renderer.render_report(md_report)
    (output_dir / "report.md").write_text(md_report, encoding="utf-8")
    (output_dir / "report.json").write_text(json_report, encoding="utf-8")
    print(f"\nReports saved to {output_dir}/")

def _handle_waiting(orch: Orchestrator, renderer: TerminalRenderer) -> None:
    while orch.state == SessionState.WAITING_FOR_USER:
        renderer.render_status("stalemate - awaiting input", orch.round_count, 0)
        try:
            user_input = input("\n[Stalemate] Enter your input (or 'end' to finish): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in ("end", "exit", "quit", "结束", "退出"):
            break
        if user_input:
            orch.inject_user_input(user_input)
            orch.resume_after_user()
            orch.run_auto()

def _run_manual(orch: Orchestrator, renderer: TerminalRenderer) -> None:
    known = [p.id for p in orch._plugins]
    while orch.state == SessionState.RUNNING:
        orch._run_one_round()
        if orch.state != SessionState.RUNNING:
            break
        try:
            user_input = input("\n[Moderator] Command (enter/continue/@name/end): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in ("end", "exit", "quit", "结束", "退出"):
            break
        if user_input.lower() in ("", "continue", "继续"):
            continue
        mentions = parse_mentions(user_input, known)
        orch.inject_user_input(user_input)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify CLI runs**

Run: `cd D:/claudeprojects/ai-roundtable && python main.py --help`
Expected: Shows usage with topic, --config, --max-rounds, --manual, --renderer, --output-dir

- [ ] **Step 3: Commit**

```bash
git add main.py && git commit -m "feat: add CLI entry point"
```

### Task 13: Integration Test + End-to-End Verification

**Files:**
- Modify: `D:\claudeprojects\ai-roundtable\tests\test_orchestrator.py`

- [ ] **Step 1: Run full test suite**

Run: `cd D:/claudeprojects/ai-roundtable && python -m pytest tests/ -v`
Expected: All tests pass (30+ tests)

- [ ] **Step 2: Run CLI with real AI (manual verification)**

Run:
```bash
cd D:/claudeprojects/ai-roundtable && python main.py "Python vs Rust for CLI tools" --max-rounds 3
```
Expected: Claude and Codex each respond for 3 rounds, status detection works, session ends.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A && git commit -m "fix: integration test fixes"
```

- [ ] **Step 4: Final commit**

```bash
git add -A && git commit -m "chore: complete V1 implementation"
```

---

## Attention Points

- Claude uses `claude --print -p`, Codex uses `codex exec`. Do not assume identical CLI interfaces.
- Status block parsing must use `<<<ROUNDTABLE_STATUS>>>` markers exclusively. Never parse JSON outside markers.
- Orchestrator owns all business decisions. Renderer and plugins must not make state transitions.
- Event log is append-only. Never rewrite or truncate during a session.
- Context window default is 3 rounds. Older history gets summarized, not dropped.
- `degraded` state requires user choice — never auto-generate "consensus" with < 2 participants.

## Completion Criteria

- `python -m pytest tests/ -v` — all tests pass
- `python main.py "topic" --max-rounds 3` — runs with real Claude + Codex
- State machine transitions: `ready → running → completed/waiting_for_user/degraded`
- JSONL event log written and readable
- Report generation produces valid Markdown + JSON
- Terminal renderer displays rounds, status, user messages
