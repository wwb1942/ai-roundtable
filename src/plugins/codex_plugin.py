from __future__ import annotations

import shutil

from src.models import ParticipantCapabilities, ParticipantTurnResult, RoundContext, ValidationResult
from src.plugins.cli_base import build_cli_prompt, protocol_violation, run_cli_turn

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
        if timeout < 1:
            raise ValueError("timeout must be positive")
        self._args = list(args) if args is not None else []
        self._timeout = timeout

    def validate(self) -> ValidationResult:
        if not shutil.which(self._command):
            return {"ok": False, "errors": [f"{self._command} CLI not found on PATH"], "warnings": []}
        return {"ok": True, "errors": [], "warnings": []}

    def start_session(self, topic: str, system_contract: str) -> str | None:
        return None

    def send_turn(self, session_id: str | None, round_context: RoundContext) -> ParticipantTurnResult:
        return run_cli_turn(
            self._command,
            [self._subcommand, *self._args],
            round_context,
            self._timeout,
        )

    def interrupt(self, session_id: str | None) -> bool:
        return False

    def resume(self, session_id: str) -> bool:
        return False

    def close_session(self, session_id: str | None) -> None:
        pass

    def _build_prompt(self, ctx: RoundContext) -> str:
        return build_cli_prompt(ctx)


def _detect_protocol_violation(content: str) -> dict | None:
    return protocol_violation(content)
