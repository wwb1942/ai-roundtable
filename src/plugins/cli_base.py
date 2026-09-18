from __future__ import annotations

import shutil
import subprocess
import time
from typing import Any

from src.models import ParticipantTurnResult, RoundContext
from src.status_detector import extract_content_without_status_blocks, parse_status_block


MAX_CAPTURED_OUTPUT = 1_000_000


def build_cli_prompt(ctx: RoundContext) -> str:
    """Build the provider-neutral prompt sent to a CLI participant."""
    parts = [
        ctx.get("system_contract", ""),
        "THIS IS THE TOPIC TO ANSWER NOW. Do not say you are ready or ask for the topic.",
        f"[TOPIC]\n{ctx['topic']}",
    ]
    if ctx["history_summary"]:
        parts.append(f"[HISTORY SUMMARY]\n{ctx['history_summary']}")
    for turn in ctx["recent_turns"]:
        parts.append(f"[{turn.get('participant_id', '?')}] {turn.get('content', '')}")
    for user_input in ctx["user_inputs"]:
        parts.append(f"[USER/MODERATOR] {user_input}")
    parts.append(f"[CURRENT INSTRUCTION]\n{ctx['turn_instruction']}")
    parts.append("Answer the topic now, then append the required ROUNDTABLE_STATUS block.")
    return "\n".join(parts)


def protocol_violation(content: str) -> dict | None:
    lowered = content.lower()
    ready_phrases = (
        "send me the topic",
        "send the topic",
        "what's the topic",
        "what is the topic",
        "ready to participate",
        "i'm ready",
        "i am ready",
    )
    if any(phrase in lowered for phrase in ready_phrases):
        return {
            "code": "protocol_violation",
            "message": "participant did not answer the topic",
            "detail": None,
        }
    return None


def _truncate(value: Any, limit: int = MAX_CAPTURED_OUTPUT) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[output truncated]"


def run_cli_turn(
    command: str,
    argv_tail: list[str],
    ctx: RoundContext,
    timeout: int,
) -> ParticipantTurnResult:
    """Run a participant CLI and normalize all process outcomes."""
    prompt = build_cli_prompt(ctx)
    resolved = shutil.which(command) or command
    start = time.monotonic()
    try:
        completed = subprocess.run(
            [resolved, *argv_tail, prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            cwd=ctx.get("working_directory"),
        )
        duration = int((time.monotonic() - start) * 1000)
        raw_output = _truncate(getattr(completed, "stdout", "")).strip()
        status_data = parse_status_block(raw_output)
        content = extract_content_without_status_blocks(raw_output)
        error = protocol_violation(content)
        if error is None and getattr(completed, "returncode", 0) != 0:
            stderr = _truncate(getattr(completed, "stderr", "")).strip() or None
            error = {
                "code": "non_zero_exit",
                "message": f"exit code {completed.returncode}",
                "detail": stderr,
            }
        return _turn_result(content, raw_output, status_data, error, duration)
    except FileNotFoundError:
        return _error_result(
            "command_not_found",
            f"{command} CLI not found when starting subprocess",
            start,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _truncate(getattr(exc, "stderr", None)).strip() or None
        return _error_result("timeout", f"timeout after {timeout}s", start, detail)
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        return _error_result("unknown", f"failed to run {command} CLI", start, str(exc) or None)


def _turn_result(
    content: str,
    raw_output: str,
    status_data: dict | None,
    error: dict | None,
    duration: int,
) -> ParticipantTurnResult:
    return ParticipantTurnResult(
        content=content,
        raw_output=raw_output,
        status=status_data["status"] if status_data else "unknown",
        status_summary=status_data.get("summary") if status_data else None,
        artifacts=[],
        error=error,
        duration_ms=duration,
        session_id=None,
        token_usage=None,
        cost_usd=None,
    )


def _error_result(
    code: str,
    message: str,
    start: float,
    detail: str | None = None,
) -> ParticipantTurnResult:
    return ParticipantTurnResult(
        content="",
        raw_output="",
        status="unknown",
        status_summary=None,
        artifacts=[],
        error={"code": code, "message": message, "detail": detail},
        duration_ms=int((time.monotonic() - start) * 1000),
        session_id=None,
        token_usage=None,
        cost_usd=None,
    )
