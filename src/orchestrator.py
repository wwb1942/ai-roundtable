from __future__ import annotations
import uuid
from typing import Any

from src.models import (
    IllegalStateTransition,
    SessionState,
    ParticipantTurnResult,
    RoundContext,
    assert_transition_allowed,
)
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
        context_window: int = 3,
        log_dir: str = "logs",
        retry_count: int = 1,
        on_event: Any | None = None,
    ) -> None:
        self._topic = topic
        self._plugins: list[Any] = list(plugins)
        self._max_rounds = max_rounds
        self._convergence_threshold = convergence_threshold
        self._stalemate_threshold = stalemate_threshold
        self._max_consecutive_failures = max_consecutive_failures
        self._retry_count = retry_count

        self._state = SessionState.READY
        self._round_count = 0
        self._end_reason: str | None = None
        self._all_turns: list[dict] = []
        self._pending_user_input: str | None = None

        self._consecutive_converging = 0
        self._consecutive_stalemate = 0
        self._plugin_failures: dict[str, int] = {}
        self._plugin_sessions: dict[str, str | None] = {}

        self._session_id = str(uuid.uuid4())
        self._ctx = ContextManager(window_size=context_window)
        self._log = EventLog(base_dir=log_dir, session_id=self._session_id)
        self._on_event = on_event

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
    def participant_ids(self) -> list[str]:
        return [p.id for p in self._plugins]

    @property
    def topic(self) -> str:
        return self._topic

    # --- Public API ---

    def start(self) -> None:
        if len(self._plugins) < 2:
            raise RuntimeError("Orchestrator requires at least 2 plugins")
        for plugin in self._plugins:
            validation = plugin.validate()
            if not validation.get("ok"):
                errors = "; ".join(validation.get("errors", []))
                raise RuntimeError(f"{plugin.id} validation failed: {errors}")
            self._plugin_sessions[plugin.id] = plugin.start_session(self._topic, self._ctx.system_contract)
        self._transition_to(SessionState.RUNNING)
        self._emit("session_started", {"topic": self._topic, "participants": [p.id for p in self._plugins]})

    def run_auto(self) -> None:
        while self._state == SessionState.RUNNING:
            if self._round_count >= self._max_rounds:
                self._end_reason = "max_rounds"
                self._finalize()
                return
            self._run_one_round()

    def run_one_round(self) -> None:
        if self._state == SessionState.RUNNING:
            self._run_one_round()

    def inject_user_input(self, text: str, mentions: list[str] | None = None) -> None:
        self._pending_user_input = text
        self._ctx.add_user_input(text)
        self._emit("user_input_injected", {"text": text, "mentions": mentions or []})

    def resume_after_user(self) -> None:
        if self._state == SessionState.WAITING_FOR_USER:
            self._consecutive_stalemate = 0
            self._transition_to(SessionState.RUNNING)
            self._emit("session_resumed", {})

    def finalize_degraded(self, mode: str = "end") -> None:
        if self._state == SessionState.WAITING_FOR_USER and self._end_reason == "degraded":
            self._emit("degraded_user_choice", {"mode": mode})
            self._finalize()

    def finalize_user_ended(self) -> None:
        if self._state == SessionState.WAITING_FOR_USER:
            self._end_reason = "user_ended"
            self._emit("user_ended_session", {})
            self._finalize()

    # --- Round flow ---

    def _run_one_round(self) -> None:
        self._round_count += 1
        results = self._collect_responses()
        if self._state == SessionState.DEGRADED:
            self._transition_to(SessionState.WAITING_FOR_USER)
            self._emit("waiting_for_user", {"reason": "degraded"})
            return
        self._analyze_round(results)
        self._decide_next_action()

    def _prepare_round(self, plugin: Any) -> RoundContext:
        instruction = (
            f"Round {self._round_count}. Discuss the topic and indicate your convergence status."
        )
        return self._ctx.build_context(
            self._topic,
            instruction,
            speaker_id=plugin.id,
            mentioned_by_user=False,
        )

    def _collect_responses(self) -> list[tuple[Any, ParticipantTurnResult]]:
        results: list[tuple[Any, ParticipantTurnResult]] = []
        active_plugins = list(self._plugins)

        for plugin in active_plugins:
            self._emit("turn_prompted", {"plugin_id": plugin.id, "round": self._round_count})
            ctx = self._prepare_round(plugin)
            result = self._send_with_retry(plugin, ctx)

            if result.get("error"):
                self._plugin_failures[plugin.id] = self._plugin_failures.get(plugin.id, 0) + 1
                self._emit("turn_failed", {"plugin_id": plugin.id, "error": result["error"]})
                if self._plugin_failures[plugin.id] >= self._max_consecutive_failures:
                    self._plugins.remove(plugin)
                    self._emit("participant_removed", {"plugin_id": plugin.id})
                    if len(self._plugins) < 2:
                        self._transition_to(SessionState.DEGRADED)
                        self._end_reason = "degraded"
                        self._emit("session_degraded", {})
                        return results
            else:
                self._plugin_failures[plugin.id] = 0
                self._emit("turn_completed", {"plugin_id": plugin.id, "status": result.get("status")})
                turn_record = {
                    "round_number": self._round_count,
                    "participant_id": plugin.id,
                    "sender": plugin.display_name,
                    "content": result["content"],
                    "status": result.get("status", "unknown"),
                }
                self._all_turns.append(turn_record)
                self._ctx.add_turn(turn_record)
                results.append((plugin, result))

        return results

    def _send_with_retry(self, plugin: Any, ctx: RoundContext) -> ParticipantTurnResult:
        attempts = self._retry_count + 1
        result: ParticipantTurnResult | None = None
        for attempt in range(attempts):
            result = plugin.send_turn(self._plugin_sessions.get(plugin.id), ctx)
            if not result.get("error"):
                return result
            if attempt < attempts - 1:
                self._emit("turn_retrying", {"plugin_id": plugin.id, "attempt": attempt + 1})
        assert result is not None
        return result

    def _analyze_round(self, results: list[tuple[Any, ParticipantTurnResult]]) -> None:
        if not results:
            return

        statuses = [r["status"] for _, r in results]

        # Fall back to heuristic if needed
        if len(results) >= 2:
            first_plugin = results[0][0]
            prev_content = self._previous_content_for(first_plugin.id)
            curr_same = results[0][1]["content"]
            response_a = curr_same
            response_b = results[1][1]["content"] if len(results) > 1 else None
            heuristic = heuristic_detect(prev_content, curr_same, response_a, response_b)
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

        if dominant != "unknown":
            for _, result in results:
                if result.get("status") == "unknown":
                    result["status"] = dominant
            for turn in self._all_turns:
                if turn.get("round_number") == self._round_count and turn.get("status") == "unknown":
                    turn["status"] = dominant

        self._emit("round_analyzed", {
            "round": self._round_count,
            "dominant_status": dominant,
            "consecutive_converging": self._consecutive_converging,
            "consecutive_stalemate": self._consecutive_stalemate,
        })
        self._emit("round_completed", {"round": self._round_count, "analysis_result": dominant})

    def _decide_next_action(self) -> None:
        if self._consecutive_converging >= self._convergence_threshold:
            self._end_reason = "converged"
            self._finalize()
        elif self._consecutive_stalemate >= self._stalemate_threshold:
            self._transition_to(SessionState.WAITING_FOR_USER)
            self._emit("waiting_for_user", {"reason": "stalemate"})

    def _previous_content_for(self, participant_id: str) -> str | None:
        for turn in reversed(self._all_turns):
            if (
                turn.get("participant_id") == participant_id
                and turn.get("round_number") != self._round_count
            ):
                return turn.get("content")
        return None

    def _finalize(self) -> None:
        self._transition_to(SessionState.SUMMARIZING)
        self._emit("session_summarizing", {})
        for plugin in self._plugins:
            try:
                plugin.close_session(self._plugin_sessions.get(plugin.id))
            except Exception:
                pass
        self._transition_to(SessionState.COMPLETED)
        self._emit("session_completed", {"end_reason": self._end_reason})

    def _emit(self, event_type: str, data: dict) -> None:
        self._log.append(event_type, data)
        if self._on_event:
            self._on_event({"type": event_type, "data": data})

    def _transition_to(self, next_state: SessionState) -> None:
        previous = self._state
        try:
            assert_transition_allowed(previous, next_state)
        except IllegalStateTransition:
            self._emit("state_transition_failed", {"from": previous.value, "to": next_state.value})
            raise
        self._state = next_state
        self._emit("state_changed", {"from": previous.value, "to": next_state.value})
