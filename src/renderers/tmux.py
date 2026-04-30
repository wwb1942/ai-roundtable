from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence


Runner = Callable[[list[str]], object]


class TmuxRenderer:
    """Create a tmux room with a left room pane and right participant panes."""

    def __init__(
        self,
        session_name: str = "ai-roundtable",
        participants: Sequence[str] = ("claude", "codex"),
        moderator_name: str = "You",
        tmux_cmd: Sequence[str] | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.session_name = session_name
        self._participants = [pid.lower() for pid in participants]
        self._moderator_name = moderator_name
        self._tmux_cmd = list(tmux_cmd or self._detect_tmux_command())
        self._runner = runner or self._run
        self._panes = {"room": f"{session_name}:0.0"}
        for index, pid in enumerate(self._participants, start=1):
            self._panes[pid] = f"{session_name}:0.{index}"

    def start(self, topic: str, participants: list[str]) -> None:
        self._call(["kill-session", "-t", self.session_name], allow_failure=True)
        self._call(["new-session", "-d", "-s", self.session_name, "-n", "room"])
        self._configure_scrollback()
        self._split_participant_panes()
        self._call(["select-pane", "-t", self._panes["room"], "-T", "Room"])
        for pid in self._participants:
            self._call(["select-pane", "-t", self._panes[pid], "-T", pid.title()])
        self.write_room(f"you: {self._moderator_name}")
        self.write_room(f"# {topic}  (participants: {', '.join(participants)})")

    def start_interactive(
        self,
        participant_commands: dict[str, str] | None = None,
        topic: str | None = None,
        room_command: str | None = None,
        working_directory: str | None = None,
    ) -> None:
        self._call(["kill-session", "-t", self.session_name], allow_failure=True)
        new_session = ["new-session", "-d", "-s", self.session_name, "-n", "room"]
        if working_directory:
            new_session.extend(["-c", working_directory])
        self._call(new_session)
        self._configure_scrollback()
        commands = participant_commands or {pid: pid for pid in self._participants}
        for index, pid in enumerate(self._participants, start=1):
            split = self._participant_split_command(index)
            if working_directory:
                split.extend(["-c", working_directory])
            self._call([*split, commands.get(pid, pid)])
        self._call(["select-pane", "-t", self._panes["room"], "-T", "Room"])
        for pid in self._participants:
            self._call(["select-pane", "-t", self._panes[pid], "-T", pid.title()])
        topic_text = topic or "Interactive AI roundtable"
        self.write_room(f"you: {self._moderator_name}")
        self.write_room(f"# {topic_text}  (participants: {', '.join(self._participants)})")
        command = room_command or f"python scripts/room_broker.py --session {self.session_name}"
        self._call(["send-keys", "-t", self._panes["room"], command, "Enter"])

    def attach_command(self) -> list[str]:
        return [*self._tmux_cmd, "attach-session", "-t", self.session_name]

    def attach(self) -> None:
        self._call(["attach-session", "-t", self.session_name])

    def write_room(self, text: str) -> None:
        self._write(self._panes["room"], text)

    def write_participant(self, participant_id: str, text: str) -> None:
        pane = self._panes.get(participant_id.lower(), self._panes["room"])
        self._write(pane, text)

    def send_prompt(self, participant_id: str, prompt: str) -> None:
        pane = self._panes.get(participant_id.lower())
        if not pane:
            raise ValueError(f"Unknown participant pane: {participant_id}")
        self._call(["send-keys", "-t", pane, prompt, "Enter"])

    def _write(self, pane: str, text: str) -> None:
        for line in text.splitlines() or [""]:
            self._call(["send-keys", "-t", pane, f"printf '%s\\n' {self._shell_quote(line)}", "Enter"])

    def _call(self, args: list[str], allow_failure: bool = False) -> None:
        try:
            self._runner([*self._tmux_cmd, *args])
        except subprocess.CalledProcessError:
            if not allow_failure:
                raise

    def _configure_scrollback(self) -> None:
        self._call(["set-option", "-t", self.session_name, "mouse", "on"])
        self._call(["set-option", "-t", self.session_name, "history-limit", "50000"])
        self._call(["setw", "-t", self.session_name, "mode-keys", "vi"])

    def _split_participant_panes(self) -> None:
        for index, _pid in enumerate(self._participants, start=1):
            self._call(self._participant_split_command(index))

    def _participant_split_command(self, index: int) -> list[str]:
        if index == 1:
            return ["split-window", "-h", "-p", "50", "-t", f"{self.session_name}:0"]
        return ["split-window", "-v", "-p", "50", "-t", f"{self.session_name}:0.{index - 1}"]

    @staticmethod
    def _run(cmd: list[str]) -> None:
        subprocess.run(cmd, check=True)

    @staticmethod
    def _detect_tmux_command() -> list[str]:
        if shutil.which("tmux"):
            return ["tmux"]
        if shutil.which("wsl.exe"):
            return ["wsl.exe", "tmux"]
        return ["tmux"]

    @staticmethod
    def _shell_quote(value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"
