from __future__ import annotations
from src.models import RoundContext, RecentTurn

SYSTEM_CONTRACT = """You are a participant in a multi-AI roundtable discussion.

Rules:
- Answer the current topic directly. Do not only say that you are ready.
- Build on, challenge, or synthesize the other participants' points when available.
- Keep the main answer concise and actionable.
- You MUST end every response with exactly one status block in this format:
<<<ROUNDTABLE_STATUS>>>
{"status": "converging|diverging|stalemate", "summary": "当前共识/分歧点"}
<<<END_STATUS>>>

Status meanings:
- converging: participants are moving toward a shared plan or conclusion.
- diverging: participants still have materially different proposals.
- stalemate: the discussion is repeating or needs user input.

The status block must be valid JSON. Do not wrap it in Markdown fences."""

class ContextManager:
    def __init__(self, window_size: int = 3, system_contract: str | None = None):
        self._window_size = window_size
        self._system_contract = system_contract or SYSTEM_CONTRACT
        self._all_turns: list[RecentTurn] = []
        self._pending_user_inputs: list[str] = []
        self._round_counter = 0

    def add_turn(self, turn: RecentTurn) -> None:
        self._all_turns.append(turn)

    def add_user_input(self, text: str) -> None:
        self._pending_user_inputs.append(text)

    @property
    def system_contract(self) -> str:
        return self._system_contract

    def build_context(
        self,
        topic: str,
        turn_instruction: str,
        speaker_id: str,
        mentioned_by_user: bool = False,
    ) -> RoundContext:
        self._round_counter += 1
        recent = self._all_turns[-self._window_size:]
        older = self._all_turns[:-self._window_size] if len(self._all_turns) > self._window_size else []
        summary = self._summarize(older, topic) if older else ""
        user_inputs = list(self._pending_user_inputs)
        self._pending_user_inputs.clear()
        return RoundContext(
            round_number=self._round_counter,
            topic=topic,
            system_contract=self._system_contract,
            history_summary=summary,
            recent_turns=recent,
            user_inputs=user_inputs,
            turn_instruction=turn_instruction,
            speaker_id=speaker_id,
            mentioned_by_user=mentioned_by_user,
        )

    def _summarize(self, turns: list[RecentTurn], topic: str) -> str:
        proposals = []
        for t in turns:
            sender = t.get("participant_id", "?")
            content = t.get("content", "")
            short = content[:100] + "..." if len(content) > 100 else content
            proposals.append(f"{sender}: {short}")
        return (
            "[Roundtable Summary]\n"
            f"- Topic: {topic}\n"
            f"- Current consensus: (pending analysis)\n"
            f"- Open disagreements: (pending analysis)\n"
            f"- User constraints: (none recorded)\n"
            f"- Important proposals:\n"
            + "\n".join(f"  - {p}" for p in proposals)
        )
