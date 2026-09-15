from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.room_store import MAX_READ_LIMIT
from src.room_store import RoomStore
from src.room_store import UnknownCursorError
from src.room_store import jsonl_file_lock


DEFAULT_KNOWN_TARGETS = ["claude", "codex"]  # fallback when no broker targets passed
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
Runner = Callable[[list[str]], object]
CaptureRunner = Callable[[list[str]], Any]
DEFAULT_CONTINUATION_MESSAGE = "请阅读上文，针对上一条发言给出你的分析：哪些点你认同，哪些点你有不同看法，并补充新的视角。"
DEFAULT_DEBATE_ROUNDS = 3
MAX_DEBATE_ROUNDS = 10
MAX_SYNC_LINES = 1000
USAGE_LINE = (
    "Usage: @claude message | @codex message | @all message | "
    "/debate [rounds] topic | /state | /sync [@claude|@codex|@all] [lines] | "
    "/verbose on|off | /quit"
)
MOJIBAKE_HINTS = (
    "\u5a34",
    "\u762f",
    "\u9365",
    "\u9428",
    "\u9a9e",
    "\u93c0",
    "\u95ab",
    "\u9471",
    "\u951b",
    "\u935c",
    "\u6d93",
    "\ufffd",
)


def load_participant_ids(config_path: Path) -> list[str]:
    try:
        import yaml
    except ModuleNotFoundError:
        return list(DEFAULT_KNOWN_TARGETS)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except (OSError, TypeError, ValueError):
        return list(DEFAULT_KNOWN_TARGETS)
    if not isinstance(loaded, dict):
        return list(DEFAULT_KNOWN_TARGETS)
    config: dict[str, Any] = loaded
    participant_ids = [
        str(participant["id"]).lower()
        for participant in config.get("participants", [])
        if isinstance(participant, dict) and participant.get("id")
    ]
    return participant_ids or list(DEFAULT_KNOWN_TARGETS)


def configure_utf8_stdio(
    stdin: Any | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> None:
    for stream in (stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, TypeError, ValueError):
            continue


def _mojibake_score(text: str) -> int:
    score = sum(text.count(hint) for hint in MOJIBAKE_HINTS)
    if score:
        score += text.count("?")
    return score


def normalize_input_text(text: str) -> str:
    if not text or _mojibake_score(text) == 0:
        return text

    best = text
    best_score = _mojibake_score(text)
    for encoding in ("gbk", "gb18030", "cp936"):
        try:
            candidate = text.encode(encoding).decode("utf-8", errors="replace")
        except UnicodeError:
            continue
        candidate_score = _mojibake_score(candidate)
        if candidate_score < best_score:
            best = candidate
            best_score = candidate_score
    return best


def read_room_input(
    prompt_text: str = "room> ",
    prompt_func: Callable[..., str] | None = None,
    input_func: Callable[[str], str] = input,
    readline_loader: Callable[[], None] | None = None,
) -> str:
    if prompt_func is None:
        try:
            from prompt_toolkit import prompt as prompt_func
        except ImportError:
            prompt_func = None

    if prompt_func is None:
        if readline_loader is None:
            readline_loader = enable_readline_editing
        readline_loader()
        return normalize_input_text(input_func(prompt_text))

    try:
        return normalize_input_text(prompt_func(prompt_text, multiline=False))
    except ImportError:
        if readline_loader is None:
            readline_loader = enable_readline_editing
        readline_loader()
        return normalize_input_text(input_func(prompt_text))


def enable_readline_editing() -> None:
    try:
        import readline  # noqa: F401
    except ImportError:
        return


def _strip_target_separators(text: str) -> str:
    return text.lstrip(" \t\r\n,\uFF0C:\uFF1A;\uFF1B\u3001")


def parse_targets(text: str, known_targets: list[str] | None = None) -> tuple[list[str], str]:
    text = normalize_input_text(text)
    known = list(dict.fromkeys(target.lower() for target in (known_targets or DEFAULT_KNOWN_TARGETS)))
    # Longest-first prevents a short id from claiming a longer one (for example
    # ``@all`` must not consume the prefix of ``@alloy``).
    candidates = sorted([*known, "all"], key=len, reverse=True)
    targets: list[str] = []
    remaining = text.strip()
    while remaining.startswith("@"):
        mention = remaining[1:]
        matched: str | None = None
        # Participant ids are ASCII command tokens. Reading only that token
        # preserves the existing ``@codex继续`` shorthand while rejecting
        # prefix collisions such as ``@claudex`` and ``@alloy``.
        token_match = re.match(r"[A-Za-z0-9_-]+", mention)
        token = token_match.group(0).lower() if token_match else ""
        for target in candidates:
            if token == target:
                matched = target
                break
        if matched is None:
            break
        remaining = remaining[len(matched) + 1 :]
        if matched == "all":
            targets = list(known)
        elif matched in known and matched not in targets:
            targets.append(matched)
        remaining = _strip_target_separators(remaining)
    return targets, remaining.strip()


def resolve_target_message(targets: list[str], message: str) -> tuple[str, bool]:
    if targets and not message:
        return DEFAULT_CONTINUATION_MESSAGE, True
    return message, False


def should_auto_debate(
    raw_text: str,
    targets: list[str],
    message: str,
    known_targets: list[str] | None = None,
) -> bool:
    return False


def format_agent_prompt(
    message: str,
    event_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    target: str | None = None,
) -> str:
    event_line = f"Target room event id: {event_id}\n" if event_id else ""
    request_line = f"Room request id: {request_id}\n" if request_id else ""
    reply_line = f"Reply correlation id (use as reply_to): {request_id}\n" if request_id else ""
    session_line = f"Room session id: {session_id}\n" if session_id else ""
    target_line = f"Your room author id: {target}\n" if target else ""
    fallback_message = base64.b64encode(message.encode("utf-8")).decode("ascii")
    return (
        "Read the shared roundtable room via the MCP tool room_read. "
        f"{event_line}"
        f"{request_line}"
        f"{reply_line}"
        f"{session_line}"
        f"{target_line}"
        f"If room_read fails, decode this UTF-8 base64 fallback room message: {fallback_message}\n"
        "Answer the target user message from the room, then post only your final visible reply with room_post. "
        "When calling room_post, include the exact request_id and reply_to values shown above, plus the session_id and your author id. "
        "Do not use shell commands, Python scripts, PowerShell, or direct file writes to post to the room. "
        "If the MCP room_post tool is unavailable or fails, print exactly this ASCII fallback format and nothing else after it. "
        "The payload must be your final visible reply encoded as UTF-8 base64:\n"
        "ROOM_POST_FALLBACK_BASE64_BEGIN\n"
        "<utf8-base64 final visible reply>\n"
        "ROOM_POST_FALLBACK_BASE64_END\n"
        "Legacy plain-text fallback is also accepted:\n"
        "ROOM_POST_FALLBACK_BEGIN\n"
        "<your final visible reply>\n"
        "ROOM_POST_FALLBACK_END"
    )


def snapshots_changed(previous: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    return {target: output for target, output in current.items() if previous.get(target) != output}


def safe_terminal_text(value: object) -> str:
    """Remove terminal control bytes from user and participant output."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x1b\r]", "", str(value or ""))


def format_room_state(broker: "RoomBroker") -> str:
    state = broker.room_store.state()
    participants = ", ".join(state["participants"]) if state["participants"] else "(none)"
    return (
        "[state] "
        f"events={state['event_count']} "
        f"messages={state['message_count']} "
        f"participants={participants} "
        f"last_event_id={state['last_event_id']}"
    )


def format_room_tail(broker: "RoomBroker", limit: int = 10) -> str:
    safe_limit = min(max(int(limit), 1), MAX_READ_LIMIT)
    events = broker.room_store.read(limit=safe_limit)
    if not events:
        return "[tail] no room messages"
    lines = [f"[tail] last {len(events)} room messages"]
    for event in events:
        author = safe_terminal_text(event.get("author") or "?")
        content = safe_terminal_text(event.get("content") or "").replace("\n", " ")
        lines.append(f"{author}: {content}")
    return "\n".join(lines)


def extract_room_replies(output: str) -> list[str]:
    replies: list[str] = []
    base64_begin = "ROOM_POST_FALLBACK_BASE64_BEGIN"
    base64_end = "ROOM_POST_FALLBACK_BASE64_END"
    search_from = 0
    while True:
        start = output.find(base64_begin, search_from)
        if start == -1:
            break
        content_start = start + len(base64_begin)
        stop = output.find(base64_end, content_start)
        if stop == -1:
            break
        encoded = re.sub(r"\s+", "", output[content_start:stop])
        try:
            content = base64.b64decode(encoded, validate=True).decode("utf-8").strip()
        except Exception:
            content = ""
        if content:
            replies.append(content)
        search_from = stop + len(base64_end)

    begin = "ROOM_POST_FALLBACK_BEGIN"
    end = "ROOM_POST_FALLBACK_END"
    search_from = 0
    while True:
        start = output.find(begin, search_from)
        if start == -1:
            break
        content_start = start + len(begin)
        stop = output.find(end, content_start)
        if stop == -1:
            break
        content = output[content_start:stop].strip()
        if content and "<your final visible reply>" not in content:
            replies.append(content)
        search_from = stop + len(end)

    legacy_markers = [
        "\u56de\u9000\u5230\u7ec8\u7aef\u8f93\u51fa\u6700\u7ec8\u56de\u590d\uff1a",
        "\u56de\u9000\u5230\u7ec8\u7aef\u8f93\u51fa\u6700\u7ec8\u56de\u590d:",
        "fallback to terminal output:",
        "terminal fallback:",
    ]
    for marker in legacy_markers:
        marker_index = output.lower().find(marker.lower())
        if marker_index == -1:
            continue
        content = output[marker_index + len(marker) :].strip()
        if content:
            replies.append(content)
            break
    return replies


ERROR_SIGNALS = (
    "API Error",
    "violate our Usage Policy",
    "UserPromptSubmit hook error",
    "Failed with non-blocking status code",
    "Run 3 stop hooks",
    "stop hooks (status: error)",
    "claude --model claude-sonnet",
    "press esc to edit your last message",
)

MCP_NOISE_SIGNALS = (
    '"room_read"',
    '"room_post"',
    '"room_state"',
    "room_read",
    "room_post",
    "room_state",
    "mcp_servers",
    "approval_mode",
    "continuation_message_id",
    "MCP tool",
    "tool_call",
    "tool_result",
)


def output_contains_error(output: str) -> bool:
    return any(signal in output for signal in ERROR_SIGNALS)


def output_is_mcp_noise(output: str) -> bool:
    mcp_hits = sum(1 for signal in MCP_NOISE_SIGNALS if signal in output)
    if mcp_hits >= 2:
        return True
    try:
        stripped = output.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            parsed = json.loads(stripped)
            if any(k in parsed for k in ("tool", "method", "jsonrpc", "room_post", "from_id")):
                return True
    except (json.JSONDecodeError, ValueError):
        pass
    return False


def output_is_noise(output: str) -> bool:
    return output_contains_error(output) or output_is_mcp_noise(output)


def extract_visible_terminal_reply(output: str) -> str:
    lines = output.replace("\r\n", "\n").splitlines()
    kept: list[str] = []
    skipping_fallback_template = False
    skip_prefixes = (
        "Read the shared roundtable room",
        "Target room event id:",
        "Room request id:",
        "Reply correlation id:",
        "Room session id:",
        "Your room author id:",
        "If room_read fails,",
        "Answer the target user message",
        "Do not use shell commands,",
        "If the MCP room_post tool",
        "The payload must be",
        "Legacy plain-text fallback",
    )
    skip_exact = {
        "ROOM_POST_FALLBACK_BASE64_BEGIN",
        "<utf8-base64 final visible reply>",
        "ROOM_POST_FALLBACK_BASE64_END",
        "ROOM_POST_FALLBACK_BEGIN",
        "<your final visible reply>",
        "ROOM_POST_FALLBACK_END",
    }

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if kept:
                kept.append("")
            continue
        if stripped in skip_exact:
            skipping_fallback_template = stripped not in {
                "ROOM_POST_FALLBACK_BASE64_END",
                "ROOM_POST_FALLBACK_END",
            }
            continue
        if skipping_fallback_template:
            continue
        if any(stripped.startswith(prefix) for prefix in skip_prefixes):
            continue
        kept.append(line.rstrip())

    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    result = "\n".join(kept).strip()
    if result and output_is_noise(result):
        return ""
    return result


class RoomBroker:
    def __init__(
        self,
        session: str,
        targets: list[str],
        log_path: Path | None = None,
        room_file: Path | None = None,
        runner: Runner | None = None,
        capture_runner: CaptureRunner | None = None,
        sleeper: Callable[[float], None] | None = None,
        logger: Callable[[dict], None] | None = None,
        verbose: bool = False,
        printer: Callable[[str], None] = print,
    ) -> None:
        if not SESSION_PATTERN.fullmatch(session):
            raise ValueError("session must contain only letters, numbers, '.', '_' or '-'")
        self.session = session
        self.verbose = verbose
        self._targets = [target.lower() for target in targets]
        self.panes = {target: f"{session}:0.{index + 1}" for index, target in enumerate(self._targets)}
        self.log_path = log_path or Path("logs") / f"room-{session}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.room_store = RoomStore(room_file or Path("logs") / f"mcp-room-{session}.jsonl", session_id=session)
        self._runner = runner or self._run
        self._capture_runner = capture_runner or self._capture
        self._sleeper = sleeper or time.sleep
        self._logger = logger or self._log
        self._printer = printer
        self._fallback_baselines: dict[str, str] = {}
        self._pending_visible_replies: dict[str, str] = {}
        self._last_prompt_by_target: dict[str, str] = {}
        self._request_id_by_event_id: dict[str, str] = {}
        self._session_marker_id: str | None = self._find_session_marker()

    @property
    def targets(self) -> list[str]:
        return list(self._targets)

    def set_verbose(self, value: bool) -> None:
        self.verbose = value
        self._printer(f"[verbose] {'on' if value else 'off'}")

    def _say_status(self, text: str) -> None:
        if self.verbose:
            self._printer(text)

    def _say_error(self, text: str) -> None:
        self._printer(text)

    def _say_chat(self, author: str, content: str) -> None:
        safe_author = safe_terminal_text(author)
        for line in safe_terminal_text(content).splitlines() or [""]:
            self._printer(f"{safe_author}> {line}")

    def last_prompt_for(self, target: str) -> str | None:
        return self._last_prompt_by_target.get(target.lower())

    def post_session_marker(self) -> dict:
        marker = self.room_store.post(
            "system",
            f"broker session started: {self.session}",
            role="system",
            session_id=self.session,
        )
        self._session_marker_id = marker["id"]
        return marker

    def _find_session_marker(self) -> str | None:
        try:
            events = self.room_store.read(limit=MAX_READ_LIMIT)
        except (OSError, ValueError):
            return None
        for event in reversed(events):
            if (
                event.get("author") == "system"
                and event.get("role") == "system"
                and event.get("session_id") in (None, self.session)
                and str(event.get("content") or "") == f"broker session started: {self.session}"
            ):
                marker_id = event.get("id")
                if isinstance(marker_id, str):
                    return marker_id
        return None

    def send(
        self,
        target: str,
        message: str,
        event_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        target_key = target.lower()
        pane = self.panes.get(target_key)
        if pane is None:
            raise ValueError(f"Unknown participant target: {target}")
        resolved_request_id = request_id or event_id
        prompt = format_agent_prompt(
            message,
            event_id=event_id,
            request_id=resolved_request_id,
            session_id=self.session,
            target=target_key,
        )
        self._last_prompt_by_target[target_key] = prompt
        self._runner(["tmux", "select-pane", "-t", pane])
        self._runner(["tmux", "send-keys", "-t", pane, "-l", prompt])
        self._sleeper(0.35)
        self._runner(["tmux", "send-keys", "-t", pane, "C-m"])
        self._logger(
            {
                "type": "message_sent",
                "target": target_key,
                "message": message,
                "request_id": resolved_request_id,
                "session_id": self.session,
            }
        )

    def send_many(self, targets: list[str], message: str) -> dict:
        normalized_targets = list(dict.fromkeys(target.lower() for target in targets))
        unknown = [target for target in normalized_targets if target not in self.panes]
        if unknown:
            raise ValueError(f"Unknown participant target(s): {', '.join(unknown)}")
        self._capture_fallback_baselines(targets)
        request_id = str(uuid4())
        event = self.room_store.post("user", message, role="user", request_id=request_id, session_id=self.session)
        self._request_id_by_event_id[event["id"]] = request_id
        for target in normalized_targets:
            self.send(target, message, event_id=event["id"], request_id=request_id)
        return event

    def room_events_after(self, after_id: str | None = None) -> list[dict]:
        try:
            return self.room_store.read(after_id=after_id, limit=100, session_id=self.session)
        except UnknownCursorError:
            # A rotated/truncated log must not silently jump to its tail. If a
            # session marker exists, explicitly resume after that marker and log
            # the recovery so callers can surface it in diagnostics.
            if self._session_marker_id and after_id != self._session_marker_id:
                self._logger(
                    {
                        "type": "room_cursor_reset",
                        "from_cursor": after_id,
                        "to_cursor": self._session_marker_id,
                        "session_id": self.session,
                    }
                )
                return self.room_store.read(after_id=self._session_marker_id, limit=100, session_id=self.session)
            raise

    def request_id_for_event(self, event_id: str | None) -> str | None:
        if event_id is None:
            return None
        request_id = self._request_id_by_event_id.get(event_id)
        if request_id:
            return request_id
        event = self.room_store.find(event_id)
        if event:
            value = event.get("request_id")
            return value if isinstance(value, str) else None
        return None

    def sync(self, targets: list[str] | None = None, lines: int = 80) -> dict[str, str]:
        selected_targets = [target.lower() for target in (targets or self.targets)]
        unknown = [target for target in selected_targets if target not in self.panes]
        if unknown:
            raise ValueError(f"Unknown participant target(s): {', '.join(unknown)}")
        safe_lines = min(max(int(lines), 1), MAX_SYNC_LINES)
        output: dict[str, str] = {}
        for target in selected_targets:
            pane = self.panes[target]
            captured = self._capture_runner(["tmux", "capture-pane", "-p", "-t", pane, "-S", f"-{safe_lines}"])
            output[target] = self._extract_stdout(captured)
            self._logger({"type": "pane_synced", "target": target, "lines": safe_lines})
        return output

    def poll_changes(
        self,
        previous: dict[str, str],
        targets: list[str] | None = None,
        lines: int = 80,
    ) -> tuple[dict[str, str], dict[str, str]]:
        current = self.sync(targets, lines)
        return current, snapshots_changed(previous, current)

    def terminal_fallback_replies(self, targets: list[str]) -> dict[str, str]:
        targets_with_baselines = [target for target in targets if target in self._fallback_baselines]
        if not targets_with_baselines:
            return {}
        try:
            current = self.sync(targets_with_baselines, lines=200)
        except Exception as exc:
            self._logger({"type": "terminal_fallback_capture_failed", "error": str(exc)})
            return {}

        replies: dict[str, str] = {}
        for target, output in current.items():
            baseline = self._fallback_baselines.get(target, "")
            changed = output[len(baseline) :] if output.startswith(baseline) else output
            last_prompt = self._last_prompt_by_target.get(target)
            if last_prompt:
                prompt_index = changed.find(last_prompt)
                if prompt_index != -1:
                    changed = changed[prompt_index + len(last_prompt) :]
                else:
                    prompt_prefix = last_prompt[:80]
                    prompt_index = changed.find(prompt_prefix)
                    if prompt_index != -1:
                        changed = changed[prompt_index + len(prompt_prefix) :]
            extracted = extract_room_replies(changed)
            if extracted:
                replies[target] = extracted[-1]
                self._pending_visible_replies.pop(target, None)
                continue
            if output_is_noise(changed):
                self._logger({"type": "terminal_fallback_skipped_noise", "target": target})
                continue
            visible_reply = extract_visible_terminal_reply(changed)
            if visible_reply and self._pending_visible_replies.get(target) == visible_reply:
                replies[target] = visible_reply
                self._pending_visible_replies.pop(target, None)
            elif visible_reply:
                self._pending_visible_replies[target] = visible_reply
        return replies

    @staticmethod
    def _run(cmd: list[str]) -> None:
        subprocess.run(cmd, check=True)

    @staticmethod
    def _capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")

    @staticmethod
    def _extract_stdout(result: Any) -> str:
        if isinstance(result, str):
            return result
        return str(getattr(result, "stdout", ""))

    def _capture_fallback_baselines(self, targets: list[str]) -> None:
        normalized_targets = [target.lower() for target in targets]
        try:
            self._fallback_baselines = self.sync(normalized_targets, lines=200)
            for target in normalized_targets:
                self._pending_visible_replies.pop(target, None)
        except Exception as exc:
            self._fallback_baselines = {}
            self._pending_visible_replies = {}
            self._logger({"type": "terminal_fallback_baseline_failed", "error": str(exc)})

    def _log(self, data: dict) -> None:
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), **data}
        with jsonl_file_lock(self.log_path):
            with open(self.log_path, "a", encoding="utf-8", newline="") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()


def parse_sync_command(text: str, known_targets: list[str] | None = None) -> tuple[list[str], int] | None:
    parts = text.strip().split()
    if not parts or parts[0] != "/sync":
        return None
    known = [target.lower() for target in (known_targets or DEFAULT_KNOWN_TARGETS)]
    targets: list[str] = []
    lines = 80
    for part in parts[1:]:
        if part.startswith("@"):
            target = part[1:].lower()
            if target == "all":
                targets = list(known)
            elif target in known and target not in targets:
                targets.append(target)
            continue
        if part.isdigit():
            value = int(part)
            if value > 0:
                lines = min(value, MAX_SYNC_LINES)
    return targets or list(known), lines


def parse_debate_command(text: str) -> tuple[int, str] | None:
    text = normalize_input_text(text).strip()
    if not text.startswith("/debate"):
        return None
    rest = text[len("/debate") :].strip()
    if not rest:
        return DEFAULT_DEBATE_ROUNDS, ""

    first, separator, remainder = rest.partition(" ")
    if first.isdigit():
        rounds = int(first)
        if 1 <= rounds <= MAX_DEBATE_ROUNDS and separator:
            return rounds, remainder.strip()
    return DEFAULT_DEBATE_ROUNDS, rest


def format_debate_turn_message(
    topic: str,
    round_number: int,
    rounds: int,
    speaker: str,
    is_first_turn: bool,
) -> str:
    header = f"Debate topic: {topic}\nRound {round_number}/{rounds}. Speaker: @{speaker}."
    language = "Reply in the same language as the user/topic unless explicitly asked otherwise."
    if is_first_turn:
        instruction = (
            "Give your opening analysis. Be concise, make one clear claim, and leave room for the other participant to add a different perspective."
        )
    elif round_number == rounds:
        instruction = (
            "Read the prior room messages. Respond to the other participant's latest analysis, address the most important remaining differences in viewpoint, then give your final position, points of agreement, points of difference, and a practical conclusion."
        )
    else:
        instruction = (
            "Read the prior room messages. Respond to the other participant: note where you have a different perspective, acknowledge points you agree with, and move the discussion forward."
        )
    return f"{header}\n{instruction}\n{language}"


def format_debate_round_message(topic: str, round_number: int, rounds: int) -> str:
    return format_debate_turn_message(
        topic=topic,
        round_number=round_number,
        rounds=rounds,
        speaker="all",
        is_first_turn=round_number == 1,
    )


def debate_turn_order(targets: list[str], round_number: int) -> list[str]:
    return list(targets)


def run_debate(
    broker: RoomBroker,
    topic: str,
    rounds: int = DEFAULT_DEBATE_ROUNDS,
    targets: list[str] | None = None,
    wait_func: Callable[[RoomBroker, str | None, list[str]], str | None] | None = None,
    printer: Callable[[str], None] = print,
) -> str | None:
    debate_targets = targets or broker.targets
    safe_rounds = max(1, min(rounds, MAX_DEBATE_ROUNDS))
    last_id: str | None = None
    wait_for_round = wait_func or wait_for_room_replies
    first_turn = True
    for round_number in range(1, safe_rounds + 1):
        round_targets = debate_turn_order(debate_targets, round_number)
        for turn_index, target in enumerate(round_targets, start=1):
            message = format_debate_turn_message(topic, round_number, safe_rounds, target, first_turn)
            event = broker.send_many([target], message)
            printer(
                f"[debate] round {round_number}/{safe_rounds} "
                f"turn {turn_index}/{len(round_targets)} sent -> {target}"
            )
            last_id = wait_for_round(broker, event["id"], [target])
            first_turn = False
    return last_id


def wait_for_room_replies(
    broker: RoomBroker,
    after_id: str | None,
    targets: list[str],
    interval: float = 1.0,
    max_seconds: float | None = None,
    heartbeat_seconds: float = 30.0,
    printer: Callable[[str], None] | None = None,
    status_printer: Callable[[str], None] | None = None,
    error_printer: Callable[[str], None] | None = None,
    reply_printer: Callable[[str, str], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    request_id: str | None = None,
) -> str | None:
    status_out = status_printer or printer or print
    error_out = error_printer or printer or print
    chat_out = reply_printer or (lambda author, content: (printer or print)(content))
    expected = {target.lower() for target in targets}
    seen: set[str] = set()
    last_id = after_id
    correlation_id = request_id or broker.request_id_for_event(after_id)
    start = clock()
    deadline = start + max_seconds if max_seconds is not None else None
    next_heartbeat = start + heartbeat_seconds
    try:
        while seen != expected and (deadline is None or clock() < deadline):
            if deadline is not None:
                remaining = deadline - clock()
                if remaining <= 0:
                    break
                sleeper(min(interval, remaining))
            else:
                sleeper(interval)
            now = clock()
            if deadline is not None and now >= deadline:
                break
            try:
                events = broker.room_events_after(last_id)
            except UnknownCursorError as exc:
                error_out(f"\n[cursor] cannot resume room events: {exc}")
                return last_id
            for event in events:
                event_id = event.get("id")
                if not isinstance(event_id, str) or not event_id:
                    error_out("\n[room] ignored event without a valid id")
                    continue
                last_id = event_id
                author = str(event.get("author") or "")
                author_key = author.lower()
                if (
                    event.get("type") not in (None, "message")
                    or event.get("role") in ("user", "system")
                    or author_key not in expected
                    or author_key in seen
                ):
                    continue
                if correlation_id:
                    reply_to = event.get("reply_to")
                    event_request_id = event.get("request_id")
                    accepted_correlations = {correlation_id}
                    if after_id:
                        # Older prompts exposed only the room event id. Accept
                        # that id as a compatibility alias while preferring the
                        # explicit request id for new clients.
                        accepted_correlations.add(after_id)
                    if reply_to not in accepted_correlations and event_request_id not in accepted_correlations:
                        continue
                    event_session = event.get("session_id")
                    if event_session is not None and event_session != broker.session:
                        continue
                seen.add(author_key)
                status_out(f"\n--- {author_key} reply ---")
                chat_out(author_key, event.get("content", ""))
                status_out(f"--- end {author_key} ---")
            missing = sorted(expected - seen)
            fallback_replies = broker.terminal_fallback_replies(missing)
            for target, content in fallback_replies.items():
                if target in seen:
                    continue
                seen.add(target)
                posted = broker.room_store.post(
                    target,
                    content,
                    session_id=broker.session,
                    request_id=correlation_id,
                    reply_to=correlation_id,
                )
                last_id = posted["id"]
                status_out(f"\n--- {target} terminal fallback ---")
                chat_out(target, content)
                status_out(f"--- end {target} terminal fallback ---")
            if seen != expected and now >= next_heartbeat:
                missing = ", ".join(sorted(expected - seen))
                status_out(f"\n[waiting] still waiting for: {missing}")
                next_heartbeat = now + heartbeat_seconds
    except KeyboardInterrupt:
        status_out("\n[cancelled] wait interrupted; returning to room prompt.")
        return last_id
    if seen != expected:
        missing = ", ".join(sorted(expected - seen))
        error_out(f"\n[timeout] timed out waiting for: {missing}")
    return last_id


def wait_for_agent_updates(*args, **kwargs):
    return wait_for_room_replies(*args, **kwargs)


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Room broker for interactive tmux roundtable.")
    parser.add_argument("--session", default="ai-roundtable")
    parser.add_argument("--topic", default="")
    parser.add_argument("--room-file", default="")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.yaml"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    room_file = Path(args.room_file) if args.room_file else None
    participant_ids = load_participant_ids(Path(args.config))
    broker = RoomBroker(args.session, targets=participant_ids, room_file=room_file, verbose=args.verbose)
    broker.post_session_marker()
    print("room ready. type /help for commands.")
    if args.topic:
        print(f"Topic: {args.topic}")

    while True:
        try:
            text = read_room_input().strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            break
        if text == "/help":
            print(USAGE_LINE)
            continue
        if text == "/verbose on":
            broker.set_verbose(True)
            continue
        if text == "/verbose off":
            broker.set_verbose(False)
            continue
        if text == "/state":
            print(format_room_state(broker))
            continue
        if text.startswith("/tail"):
            parts = text.split()
            limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
            print(format_room_tail(broker, limit=limit))
            continue
        sync_args = parse_sync_command(text, broker.targets)
        if sync_args:
            targets, lines = sync_args
            synced = broker.sync(targets, lines)
            for target, output in synced.items():
                print(f"\n--- {target} pane last {lines} lines ---")
                print(output.rstrip() or "(empty)")
                print(f"--- end {target} ---")
            continue
        debate_args = parse_debate_command(text)
        if debate_args:
            rounds, topic = debate_args
            if not topic:
                print("Use /debate [rounds] followed by a topic.")
                continue
            run_debate(broker, topic, rounds=rounds, printer=broker._say_status)
            continue
        targets, message = parse_targets(text, broker.targets)
        if not targets:
            broker._say_error(f"Use @{', @'.join(broker.targets)}, or @all followed by a message.")
            continue
        debate_args = parse_debate_command(message)
        if debate_args:
            rounds, topic = debate_args
            if not topic:
                broker._say_error("Use @claude, @codex, or @all /debate [rounds] followed by a topic.")
                continue
            run_debate(broker, topic, rounds=rounds, targets=targets, printer=broker._say_status)
            continue
        if should_auto_debate(text, targets, message, broker.targets):
            broker._say_status(f"[debate] @all defaults to {DEFAULT_DEBATE_ROUNDS} debate rounds.")
            run_debate(broker, message, rounds=DEFAULT_DEBATE_ROUNDS, targets=targets, printer=broker._say_status)
            continue
        message, generated = resolve_target_message(targets, message)
        if generated:
            broker._say_status(f"[input] no message supplied; asking {', '.join(targets)} to continue from context.")
        user_event = broker.send_many(targets, message)
        broker._say_status(f"sent -> {', '.join(targets)}")
        wait_for_room_replies(
            broker,
            user_event["id"],
            targets,
            status_printer=broker._say_status,
            error_printer=broker._say_error,
            reply_printer=broker._say_chat,
        )


if __name__ == "__main__":
    main()
