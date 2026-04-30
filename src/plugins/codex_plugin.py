from __future__ import annotations
import shutil
import subprocess
import time
from src.models import ParticipantCapabilities, ParticipantTurnResult, RoundContext, ValidationResult
from src.status_detector import extract_content_without_status_blocks, parse_status_block

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

    def __init__(
        self,
        command: str = "codex",
        subcommand: str = "exec",
        args: list[str] | None = None,
        timeout: int = 120,
    ):
        self._command = command
        self._subcommand = subcommand
        self._args = args if args is not None else []
        self._timeout = timeout

    def validate(self) -> ValidationResult:
        if not shutil.which(self._command):
            return {"ok": False, "errors": [f"{self._command} CLI not found on PATH"], "warnings": []}
        return {"ok": True, "errors": [], "warnings": []}

    def start_session(self, topic: str, system_contract: str) -> str | None:
        return None

    def send_turn(self, session_id: str | None, round_context: RoundContext) -> ParticipantTurnResult:
        prompt = self._build_prompt(round_context)
        command = shutil.which(self._command) or self._command
        start = time.monotonic()
        try:
            result = subprocess.run(
                [command, self._subcommand, *self._args, prompt],
                capture_output=True, text=True, timeout=self._timeout,
                encoding="utf-8",
            )
            duration = int((time.monotonic() - start) * 1000)
            output = result.stdout.strip()
            status_data = parse_status_block(output)
            content = extract_content_without_status_blocks(output)
            protocol_error = _detect_protocol_violation(content)
            error = None
            if protocol_error:
                error = protocol_error
            elif result.returncode != 0:
                error = {
                    "code": "non_zero_exit",
                    "message": f"exit code {result.returncode}",
                    "detail": result.stderr.strip() or None,
                }
            return ParticipantTurnResult(
                content=content, raw_output=output,
                status=status_data["status"] if status_data else "unknown",
                status_summary=status_data.get("summary") if status_data else None,
                artifacts=[], error=error,
                duration_ms=duration,
                session_id=None,
                token_usage=None,
                cost_usd=None,
            )
        except FileNotFoundError:
            duration = int((time.monotonic() - start) * 1000)
            return ParticipantTurnResult(
                content="", raw_output="", status="unknown",
                status_summary=None, artifacts=[],
                error={
                    "code": "command_not_found",
                    "message": f"{self._command} CLI not found when starting subprocess",
                    "detail": None,
                },
                duration_ms=duration,
                session_id=None,
                token_usage=None,
                cost_usd=None,
            )
        except subprocess.TimeoutExpired:
            duration = int((time.monotonic() - start) * 1000)
            return ParticipantTurnResult(
                content="", raw_output="", status="unknown",
                status_summary=None, artifacts=[],
                error={
                    "code": "timeout",
                    "message": f"timeout after {self._timeout}s",
                    "detail": None,
                },
                duration_ms=duration,
                session_id=None,
                token_usage=None,
                cost_usd=None,
            )

    def interrupt(self, session_id: str | None) -> bool:
        return False

    def resume(self, session_id: str) -> bool:
        return False

    def close_session(self, session_id: str | None) -> None:
        pass

    def _build_prompt(self, ctx: RoundContext) -> str:
        parts = [
            ctx.get("system_contract", ""),
            "THIS IS THE TOPIC TO ANSWER NOW. Do not say you are ready or ask for the topic.",
            f"[TOPIC]\n{ctx['topic']}",
        ]
        if ctx["history_summary"]:
            parts.append(f"[HISTORY SUMMARY]\n{ctx['history_summary']}")
        for turn in ctx["recent_turns"]:
            parts.append(f"[{turn.get('participant_id', '?')}] {turn.get('content', '')}")
        for ui in ctx["user_inputs"]:
            parts.append(f"[USER/MODERATOR] {ui}")
        parts.append(f"[CURRENT INSTRUCTION]\n{ctx['turn_instruction']}")
        parts.append("Answer the topic now, then append the required ROUNDTABLE_STATUS block.")
        return "\n".join(parts)


def _detect_protocol_violation(content: str) -> dict | None:
    lowered = content.lower()
    ready_phrases = [
        "send me the topic",
        "send the topic",
        "what's the topic",
        "what is the topic",
        "ready to participate",
        "i'm ready",
        "i am ready",
    ]
    if any(phrase in lowered for phrase in ready_phrases):
        return {
            "code": "protocol_violation",
            "message": "participant did not answer the topic",
            "detail": None,
        }
    return None
