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
