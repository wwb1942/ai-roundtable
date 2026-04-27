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
