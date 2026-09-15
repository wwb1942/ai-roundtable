from __future__ import annotations

from copy import deepcopy

from src.models import RoundContext, RecentTurn

MAX_SUMMARY_CHARS = 4_000
MAX_RETAINED_USER_INPUTS = 100

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
        if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size < 1:
            raise ValueError("window_size must be a positive integer")
        self._window_size = window_size
        self._system_contract = system_contract or SYSTEM_CONTRACT
        self._all_turns: list[RecentTurn] = []
        self._pending_user_inputs: list[str] = []
        self._all_user_inputs: list[str] = []
        self._round_counter = 0

    def add_turn(self, turn: RecentTurn) -> None:
        self._all_turns.append(turn)

    def add_user_input(self, text: str) -> None:
        normalized = str(text)
        self._pending_user_inputs.append(normalized)
        self._all_user_inputs.append(normalized)
        if len(self._all_user_inputs) > MAX_RETAINED_USER_INPUTS:
            del self._all_user_inputs[:-MAX_RETAINED_USER_INPUTS]

    @property
    def system_contract(self) -> str:
        return self._system_contract

    def build_context(
        self,
        topic: str,
        turn_instruction: str,
        speaker_id: str,
        mentioned_by_user: bool = False,
        round_number: int | None = None,
    ) -> RoundContext:
        """Build a context snapshot for one participant turn.

        ``round_number`` is optional for backwards compatibility with callers
        that use ``ContextManager`` directly.  The orchestrator supplies it so
        every participant in a round receives the same number instead of
        advancing the counter once per participant.
        """
        if round_number is None:
            self._round_counter += 1
            resolved_round_number = self._round_counter
        else:
            if round_number < 1:
                raise ValueError("round_number must be positive")
            self._round_counter = max(self._round_counter, round_number)
            resolved_round_number = round_number

        # The window is measured in discussion rounds, not individual turns.
        # A round can contain several participants, all of whom must remain
        # visible together in the next context snapshot.
        round_numbers: list[int] = []
        for turn in self._all_turns:
            try:
                round_number = int(turn.get("round_number", 0))
            except (TypeError, ValueError):
                round_number = 0
            if round_number not in round_numbers:
                round_numbers.append(round_number)
        recent_rounds = set(round_numbers[-self._window_size :])
        recent_source = [turn for turn in self._all_turns if self._turn_round(turn) in recent_rounds]
        older = [turn for turn in self._all_turns if self._turn_round(turn) not in recent_rounds]
        recent = deepcopy(recent_source)
        summary = self._summarize(older, topic) if older else ""
        user_inputs = list(self._pending_user_inputs)
        self._pending_user_inputs.clear()
        return RoundContext(
            round_number=resolved_round_number,
            topic=topic,
            system_contract=self._system_contract,
            history_summary=summary,
            recent_turns=recent,
            user_inputs=user_inputs,
            turn_instruction=turn_instruction,
            speaker_id=speaker_id,
            mentioned_by_user=mentioned_by_user,
        )

    @staticmethod
    def for_speaker(
        snapshot: RoundContext,
        speaker_id: str,
        mentioned_by_user: bool | None = None,
    ) -> RoundContext:
        """Return an isolated speaker view of a previously built snapshot.

        A round is prepared before any participant is called.  Each call gets
        a deep copy so plugins cannot leak mutations into another participant's
        context or into the next round's history.
        """
        context = deepcopy(snapshot)
        context["speaker_id"] = speaker_id
        if mentioned_by_user is not None:
            context["mentioned_by_user"] = mentioned_by_user
        return context

    def _summarize(self, turns: list[RecentTurn], topic: str) -> str:
        # Keep the summary deterministic and bounded. The full recent window is
        # supplied separately; older turns contribute only compact evidence.
        status_counts: dict[str, int] = {}
        proposals: list[str] = []
        for t in turns[-50:]:
            sender = t.get("participant_id", "?")
            content = str(t.get("content") or "")
            short = content[:100] + "..." if len(content) > 100 else content
            proposals.append(f"{sender}: {short}")
            status = t.get("status")
            if status:
                status_counts[str(status)] = status_counts.get(str(status), 0) + 1
        consensus = ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items())) or "unknown"
        constraints = self._all_user_inputs[-5:]
        summary = (
            "[Roundtable Summary]\n"
            f"- Topic: {topic}\n"
            f"- Current consensus status evidence: {consensus}\n"
            f"- Open disagreements: {'present' if status_counts.get('diverging') else 'none recorded'}\n"
            f"- User constraints: {', '.join(constraints) if constraints else '(none recorded)'}\n"
            f"- Important proposals:\n"
            + "\n".join(f"  - {p}" for p in proposals)
        )
        return summary[:MAX_SUMMARY_CHARS]

    @staticmethod
    def _turn_round(turn: RecentTurn) -> int:
        try:
            return int(turn.get("round_number", 0))
        except (TypeError, ValueError):
            return 0
