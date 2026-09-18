from __future__ import annotations
import logging
import time
import uuid
from typing import Any, Callable

from src.models import (
    IllegalStateTransition,
    SessionState,
    ParticipantTurnResult,
    RoundContext,
    assert_transition_allowed,
)
from src.context_manager import ContextManager
from src.event_log import EventLog
from src.status_detector import heuristic_detect_many


_LOGGER = logging.getLogger(__name__)


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
        system_contract: str | None = None,
        instruction_factory: Callable[[int, str], str] | None = None,
        working_directory: str | None = None,
    ) -> None:
        self._topic = topic
        self._plugins: list[Any] = list(plugins)
        self._max_rounds = max_rounds
        self._convergence_threshold = convergence_threshold
        self._stalemate_threshold = stalemate_threshold
        self._max_consecutive_failures = max_consecutive_failures
        self._retry_count = retry_count
        self._instruction_factory = instruction_factory
        self._working_directory = working_directory

        self._state = SessionState.READY
        self._round_count = 0
        self._end_reason: str | None = None
        self._all_turns: list[dict] = []
        self._pending_user_input: str | None = None
        self._pending_mentions: set[str] = set()
        self._round_mentions: set[str] = set()
        self._all_participant_names = [getattr(plugin, "display_name", str(getattr(plugin, "id", "?"))) for plugin in self._plugins]

        self._consecutive_converging = 0
        self._consecutive_stalemate = 0
        self._plugin_failures: dict[str, int] = {}
        self._plugin_sessions: dict[str, str | None] = {}
        # Keep the exact plugin/session pairs that successfully started.  The
        # active plugin list can shrink during degraded-mode handling, but all
        # started sessions still need to be closed during finalization.
        self._started_sessions: list[tuple[Any, str | None]] = []

        self._session_id = str(uuid.uuid4())
        self._ctx = ContextManager(window_size=context_window, system_contract=system_contract)
        self._log = EventLog(base_dir=log_dir, session_id=self._session_id)
        self._on_event = on_event
        if self._max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        if self._convergence_threshold < 1 or self._stalemate_threshold < 1:
            raise ValueError("convergence and stalemate thresholds must be positive")
        if self._max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be positive")
        if self._retry_count < 0 or self._retry_count > 10:
            raise ValueError("retry_count must be between 0 and 10")
        if context_window < 1:
            raise ValueError("context_window must be positive")

    # --- Properties ---

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def round_count(self) -> int:
        return self._round_count

    @property
    def max_rounds(self) -> int:
        return self._max_rounds

    @property
    def end_reason(self) -> str | None:
        return self._end_reason

    @property
    def turns(self) -> list[dict]:
        return list(self._all_turns)

    @property
    def participant_names(self) -> list[str]:
        return list(self._all_participant_names)

    @property
    def participant_ids(self) -> list[str]:
        return [p.id for p in self._plugins]

    @property
    def topic(self) -> str:
        return self._topic

    # --- Public API ---

    def start(self) -> None:
        if self._state != SessionState.READY:
            raise RuntimeError(f"Orchestrator can only start from ready state (current: {self._state.value})")
        if len(self._plugins) < 2:
            raise RuntimeError("Orchestrator requires at least 2 plugins")
        try:
            # Validate the complete participant set before creating any
            # sessions, then make session creation transactional.
            for plugin in self._plugins:
                validation = plugin.validate()
                if not validation.get("ok"):
                    errors = "; ".join(validation.get("errors", []))
                    raise RuntimeError(f"{plugin.id} validation failed: {errors}")

            for plugin in self._plugins:
                session_id = plugin.start_session(self._topic, self._ctx.system_contract)
                self._plugin_sessions[plugin.id] = session_id
                self._started_sessions.append((plugin, session_id))

            self._transition_to(SessionState.RUNNING)
            self._emit("session_started", {"topic": self._topic, "participants": [p.id for p in self._plugins]})
        except Exception as exc:
            self._close_started_sessions()
            self._end_reason = "start_failed"
            if self._state not in {SessionState.FAILED, SessionState.COMPLETED}:
                self._transition_to(SessionState.FAILED)
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"Failed to start roundtable: {exc}") from exc

    def run_auto(self) -> None:
        while self._state == SessionState.RUNNING:
            if self._round_count >= self._max_rounds:
                self._end_reason = "max_rounds"
                self._finalize()
                return
            self._run_one_round()

    def run_one_round(self) -> None:
        if self._state == SessionState.RUNNING:
            if self._round_count >= self._max_rounds:
                self._end_reason = "max_rounds"
                self._finalize()
                return
            self._run_one_round()

    def inject_user_input(self, text: str, mentions: list[str] | None = None) -> None:
        self._pending_user_input = text
        self._pending_mentions = {str(mention).lower() for mention in (mentions or [])}
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
        if self._state in {SessionState.RUNNING, SessionState.WAITING_FOR_USER, SessionState.DEGRADED}:
            self._end_reason = "user_ended"
            self._emit("user_ended_session", {})
            self._finalize()

    def finalize_for_workflow(self, end_reason: str | None = None) -> None:
        """Close an active session without overwriting its workflow reason.

        Batch workflow callers may need to clean up a waiting or degraded
        session after automatic execution.  Unlike ``finalize_user_ended``,
        this method preserves an already-recorded reason and only supplies the
        provided fallback when the session has not recorded one yet.
        """
        if self._state in {SessionState.RUNNING, SessionState.WAITING_FOR_USER, SessionState.DEGRADED}:
            if self._end_reason is None and end_reason is not None:
                self._end_reason = end_reason
            self._emit("workflow_session_cleanup", {"end_reason": self._end_reason})
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

    def _prepare_round_snapshot(self) -> RoundContext:
        instruction = (
            f"Round {self._round_count}. Discuss the topic and indicate your convergence status."
        )
        snapshot = self._ctx.build_context(
            self._topic,
            instruction,
            speaker_id="",
            mentioned_by_user=False,
            round_number=max(1, self._round_count),
        )
        if self._working_directory is not None:
            snapshot["working_directory"] = self._working_directory
        self._round_mentions = self._pending_mentions
        self._pending_mentions = set()
        return snapshot

    def _prepare_round(self, plugin: Any, snapshot: RoundContext | None = None) -> RoundContext:
        """Build a participant view, preserving the historical helper API."""
        if snapshot is None:
            snapshot = self._prepare_round_snapshot()
        context = self._ctx.for_speaker(snapshot, plugin.id, plugin.id.lower() in self._round_mentions)
        if self._instruction_factory is not None:
            context["turn_instruction"] = self._instruction_factory(self._round_count, plugin.id)
        return context

    def _collect_responses(self) -> list[tuple[Any, ParticipantTurnResult]]:
        results: list[tuple[Any, ParticipantTurnResult]] = []
        active_plugins = list(self._plugins)
        # Freeze history and drain moderator input once for the whole round.
        # Participant responses are appended only after their contexts have
        # been created, so later participants do not observe a partial round.
        round_snapshot = self._prepare_round_snapshot()

        for plugin in active_plugins:
            self._emit("turn_prompted", {"plugin_id": plugin.id, "round": self._round_count})
            ctx = self._prepare_round(plugin, round_snapshot)
            result = self._send_with_retry(plugin, ctx)

            if result.get("error"):
                self._plugin_failures[plugin.id] = self._plugin_failures.get(plugin.id, 0) + 1
                self._emit("turn_failed", {"plugin_id": plugin.id, "error": result["error"]})
                error_turn = {
                    "round_number": self._round_count,
                    "participant_id": plugin.id,
                    "sender": plugin.display_name,
                    "content": "",
                    "status": "unknown",
                    "error": result["error"],
                }
                self._all_turns.append(error_turn)
                self._ctx.add_turn(error_turn)
                results.append((plugin, result))
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
                if result.get("status") not in {"converging", "diverging", "stalemate", "unknown"}:
                    result["status"] = "unknown"
                self._emit("turn_completed", {"plugin_id": plugin.id, "status": result.get("status")})
                turn_record = {
                    "round_number": self._round_count,
                    "participant_id": plugin.id,
                    "sender": plugin.display_name,
                    "content": result["content"],
                    "status": result.get("status", "unknown"),
                    "status_summary": result.get("status_summary"),
                }
                self._all_turns.append(turn_record)
                self._ctx.add_turn(turn_record)
                results.append((plugin, result))

        return results

    def _send_with_retry(self, plugin: Any, ctx: RoundContext) -> ParticipantTurnResult:
        attempts = self._retry_count + 1
        result: ParticipantTurnResult | None = None
        for attempt in range(attempts):
            started_at = time.monotonic()
            try:
                candidate = plugin.send_turn(self._plugin_sessions.get(plugin.id), ctx)
                if not isinstance(candidate, dict):
                    raise TypeError("participant send_turn must return a mapping")
                if not isinstance(candidate.get("content"), str):
                    raise TypeError("participant result content must be a string")
                result = candidate
            except Exception as exc:
                result = self._exception_turn_result(
                    exc,
                    int((time.monotonic() - started_at) * 1000),
                    self._plugin_sessions.get(plugin.id),
                )
            if not result.get("error"):
                return result
            if attempt < attempts - 1:
                self._emit("turn_retrying", {"plugin_id": plugin.id, "attempt": attempt + 1})
        assert result is not None
        return result

    @staticmethod
    def _exception_turn_result(
        exc: Exception,
        duration_ms: int,
        session_id: str | None = None,
    ) -> ParticipantTurnResult:
        message = str(exc) or exc.__class__.__name__
        return ParticipantTurnResult(
            content="",
            raw_output="",
            status="unknown",
            status_summary=None,
            artifacts=[],
            error={
                "code": "unknown",
                "message": "participant send_turn raised an exception",
                "detail": f"{exc.__class__.__name__}: {message}",
            },
            duration_ms=duration_ms,
            session_id=session_id,
            token_usage=None,
            cost_usd=None,
        )

    def _analyze_round(self, results: list[tuple[Any, ParticipantTurnResult]]) -> None:
        if not results:
            return

        valid_statuses = {"converging", "diverging", "stalemate", "unknown"}
        statuses = [r.get("status", "unknown") if r.get("status") in valid_statuses else "unknown" for _, r in results]
        has_errors = any(result.get("error") for _, result in results)

        paired_content = [
            (self._previous_content_for(plugin.id), result.get("content"))
            for plugin, result in results
            if result.get("content")
        ]
        previous = [previous_content for previous_content, _ in paired_content]
        responses = [content for _, content in paired_content]
        heuristic = heuristic_detect_many(previous, responses)

        # Determine a conservative dominant status from every participant.
        # A tie or a mixed converging/diverging signal must not end the session
        # as consensus; stalemate is the safe choice when any participant asks
        # for user input.
        counts = {status: statuses.count(status) for status in valid_statuses}
        participant_count = len(statuses)
        if has_errors:
            # A heuristic match among the successful replies cannot establish
            # consensus while another participant failed to answer.
            dominant = "unknown"
        elif counts["converging"] == participant_count:
            dominant = "converging"
        elif counts["stalemate"] == participant_count:
            dominant = "stalemate"
        elif counts["stalemate"]:
            dominant = "stalemate"
        elif counts["diverging"] and counts["converging"]:
            dominant = "diverging"
        elif counts["converging"] > participant_count / 2 and counts["diverging"] == 0 and counts["unknown"] == 0:
            dominant = "converging"
        elif counts["diverging"] >= participant_count / 2:
            dominant = "diverging"
        elif counts["unknown"] == participant_count:
            dominant = heuristic
        else:
            dominant = "unknown"

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
            self._end_reason = "stalemate"
            self._transition_to(SessionState.WAITING_FOR_USER)
            self._emit("waiting_for_user", {"reason": "stalemate"})

    def _previous_content_for(self, participant_id: str) -> str | None:
        for turn in reversed(self._all_turns):
            if (
                turn.get("participant_id") == participant_id
                and turn.get("round_number") != self._round_count
                and turn.get("content")
            ):
                return turn.get("content")
        return None

    def _finalize(self) -> None:
        self._transition_to(SessionState.SUMMARIZING)
        self._emit("session_summarizing", {})
        self._close_started_sessions()
        self._transition_to(SessionState.COMPLETED)
        self._emit("session_completed", {"end_reason": self._end_reason})

    def _close_started_sessions(self) -> None:
        """Close every session created by this orchestrator, once."""
        started_sessions = self._started_sessions
        self._started_sessions = []
        for plugin, session_id in reversed(started_sessions):
            try:
                plugin.close_session(session_id)
            except Exception as exc:
                # Cleanup must not mask the original roundtable outcome, but
                # the failure remains visible in the event stream for repair.
                self._emit(
                    "session_close_failed",
                    {
                        "plugin_id": getattr(plugin, "id", "unknown"),
                        "error": {"message": str(exc) or exc.__class__.__name__},
                    },
                )
        self._plugin_sessions.clear()

    def _emit(self, event_type: str, data: dict) -> None:
        self._log.append(event_type, data)
        if self._on_event:
            try:
                self._on_event({"type": event_type, "data": data})
            except Exception as exc:
                # Rendering/telemetry must not alter the state machine or stop
                # cleanup of provider sessions.
                _LOGGER.warning("event callback failed for %s: %s", event_type, exc)

    def _transition_to(self, next_state: SessionState) -> None:
        previous = self._state
        try:
            assert_transition_allowed(previous, next_state)
        except IllegalStateTransition:
            self._emit("state_transition_failed", {"from": previous.value, "to": next_state.value})
            raise
        self._state = next_state
        self._emit("state_changed", {"from": previous.value, "to": next_state.value})
