from __future__ import annotations
import uuid
import tempfile
from typing import Any

from src.models import SessionState, ParticipantTurnResult, RoundContext
from src.context_manager import ContextManager
from src.event_log import EventLog
from src.status_detector import heuristic_detect


class Orchestrator:
    def __init__(
        self,
        topic: str,
        plugins: list[Any],
        max_rounds: int = 10,
        convergence_threshold: int = 3,
        stalemate_threshold: int = 3,
        max_consecutive_failures: int = 3,
    ) -> None:
        self._topic = topic
        self._plugins: list[Any] = list(plugins)
        self._max_rounds = max_rounds
        self._convergence_threshold = convergence_threshold
        self._stalemate_threshold = stalemate_threshold
        self._max_consecutive_failures = max_consecutive_failures

        self._state = SessionState.READY
        self._round_count = 0
        self._end_reason: str | None = None
        self._all_turns: list[dict] = []
        self._pending_user_input: str | None = None

        self._consecutive_converging = 0
        self._consecutive_stalemate = 0
        self._plugin_failures: dict[str, int] = {}

        self._session_id = str(uuid.uuid4())
        self._ctx = ContextManager()
        self._log = EventLog(base_dir=tempfile.mkdtemp(), session_id=self._session_id)

    # --- Properties ---

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def round_count(self) -> int:
        return self._round_count

    @property
    def end_reason(self) -> str | None:
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

    # --- Public API ---

    def start(self) -> None:
        if len(self._plugins) < 2:
            raise RuntimeError("Orchestrator requires at least 2 plugins")
        for plugin in self._plugins:
            plugin.validate()
            plugin.start_session(self._topic)
        self._state = SessionState.RUNNING
        self._log.append("session_started", {"topic": self._topic, "participants": [p.id for p in self._plugins]})

    def run_auto(self) -> None:
        while self._state == SessionState.RUNNING:
            if self._round_count >= self._max_rounds:
                self._end_reason = "max_rounds"
                self._finalize()
                return
            self._run_one_round()

    def inject_user_input(self, text: str) -> None:
        self._pending_user_input = text
        self._ctx.add_user_input(text)
        self._log.append("user_input_injected", {"text": text})

    def resume_after_user(self) -> None:
        if self._state == SessionState.WAITING_FOR_USER:
            self._consecutive_stalemate = 0
            self._state = SessionState.RUNNING
            self._log.append("session_resumed", {})

    # --- Round flow ---

    def _run_one_round(self) -> None:
        self._round_count += 1
        ctx = self._prepare_round()
        results = self._collect_responses(ctx)
        if self._state == SessionState.DEGRADED:
            return
        self._analyze_round(results)
        self._decide_next_action()

    def _prepare_round(self) -> RoundContext:
        instruction = (
            f"Round {self._round_count}. Discuss the topic and indicate your convergence status."
        )
        return self._ctx.build_context(self._topic, instruction)

    def _collect_responses(self, ctx: RoundContext) -> list[tuple[Any, ParticipantTurnResult]]:
        results: list[tuple[Any, ParticipantTurnResult]] = []
        active_plugins = list(self._plugins)

        for plugin in active_plugins:
            self._log.append("turn_prompted", {"plugin_id": plugin.id, "round": self._round_count})
            result: ParticipantTurnResult = plugin.send_turn(ctx)

            if result.get("error"):
                self._plugin_failures[plugin.id] = self._plugin_failures.get(plugin.id, 0) + 1
                self._log.append("turn_failed", {"plugin_id": plugin.id, "error": result["error"]})
                if self._plugin_failures[plugin.id] >= self._max_consecutive_failures:
                    self._plugins.remove(plugin)
                    self._log.append("plugin_removed", {"plugin_id": plugin.id})
                    if len(self._plugins) < 2:
                        self._state = SessionState.DEGRADED
                        self._end_reason = "degraded"
                        self._log.append("session_degraded", {})
                        return results
            else:
                self._plugin_failures[plugin.id] = 0
                self._log.append("turn_completed", {"plugin_id": plugin.id, "status": result.get("status")})
                turn_record = {
                    "round": self._round_count,
                    "sender": plugin.display_name,
                    "content": result["content"],
                    "status": result.get("status"),
                }
                self._all_turns.append(turn_record)
                self._ctx.add_turn(turn_record)
                results.append((plugin, result))

        return results

    def _analyze_round(self, results: list[tuple[Any, ParticipantTurnResult]]) -> None:
        if not results:
            return

        statuses = [r["status"] for _, r in results]

        # Fall back to heuristic if needed
        if len(results) >= 2:
            prev_turns = [t for t in self._all_turns if t["round"] == self._round_count - 1]
            prev_content = prev_turns[0]["content"] if prev_turns else None
            curr_a = results[0][1]["content"]
            curr_b = results[1][1]["content"] if len(results) > 1 else None
            heuristic = heuristic_detect(prev_content, curr_a, curr_a, curr_b)
        else:
            heuristic = "unknown"

        # Determine dominant status
        if all(s == "converging" for s in statuses):
            dominant = "converging"
        elif all(s == "stalemate" for s in statuses):
            dominant = "stalemate"
        elif "stalemate" in statuses:
            dominant = "stalemate"
        elif "converging" in statuses:
            dominant = "converging"
        else:
            dominant = heuristic

        if dominant == "converging":
            self._consecutive_converging += 1
            self._consecutive_stalemate = 0
        elif dominant == "stalemate":
            self._consecutive_stalemate += 1
            self._consecutive_converging = 0
        else:
            self._consecutive_converging = 0
            self._consecutive_stalemate = 0

        self._log.append("round_analyzed", {
            "round": self._round_count,
            "dominant_status": dominant,
            "consecutive_converging": self._consecutive_converging,
            "consecutive_stalemate": self._consecutive_stalemate,
        })

    def _decide_next_action(self) -> None:
        if self._consecutive_converging >= self._convergence_threshold:
            self._end_reason = "converged"
            self._finalize()
        elif self._consecutive_stalemate >= self._stalemate_threshold:
            self._state = SessionState.WAITING_FOR_USER
            self._log.append("waiting_for_user", {"reason": "stalemate"})

    def _finalize(self) -> None:
        self._state = SessionState.SUMMARIZING
        self._log.append("session_summarizing", {})
        for plugin in self._plugins:
            try:
                plugin.close_session()
            except Exception:
                pass
        self._state = SessionState.COMPLETED
        self._log.append("session_completed", {"end_reason": self._end_reason})
