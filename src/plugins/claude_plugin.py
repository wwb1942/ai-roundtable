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
