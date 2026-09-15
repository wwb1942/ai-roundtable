from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

from src.mention import parse_mentions
from src.models import SessionState
from src.orchestrator import Orchestrator
from src.plugins.claude_plugin import ClaudePlugin
from src.plugins.codex_plugin import CodexPlugin
from src.renderers.terminal import TerminalRenderer
from src.renderers.tmux import TmuxRenderer
from src.report import generate_report


PLUGIN_REGISTRY = {"claude": ClaudePlugin, "codex": CodexPlugin}
PARTICIPANT_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
SESSION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class ConfigError(RuntimeError):
    """Raised when the YAML configuration cannot safely be used."""


# Keep command matching independent of source-file encoding artifacts in old
# generated configs.
END_COMMANDS = {"end", "exit", "quit", "\u7ed3\u675f", "\u9000\u51fa"}
CONTINUE_COMMANDS = {"", "continue", "\u7ee7\u7eed"}


def load_config(path: str) -> dict:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing dependency: PyYAML. Install with `python -m pip install -r requirements.txt`.") from exc

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except OSError as exc:
        raise ConfigError(f"Could not read config {path}: {exc}") from exc
    except Exception as exc:
        raise ConfigError(f"Could not parse config {path}: {exc}") from exc
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ConfigError("Configuration root must be a YAML mapping")
    return config


def validate_config(config: dict, plugin_registry: dict | None = None) -> dict:
    """Validate and normalize user configuration before creating any panes."""
    registry = PLUGIN_REGISTRY if plugin_registry is None else plugin_registry
    participants = config.get("participants")
    if not isinstance(participants, list) or len(participants) < 2:
        raise ConfigError("participants must contain at least two entries")

    seen: set[str] = set()
    for index, participant in enumerate(participants):
        prefix = f"participants[{index}]"
        if not isinstance(participant, dict):
            raise ConfigError(f"{prefix} must be a mapping")
        participant_id = participant.get("id")
        if not isinstance(participant_id, str) or not PARTICIPANT_ID_PATTERN.fullmatch(participant_id):
            raise ConfigError(f"{prefix}.id must match {PARTICIPANT_ID_PATTERN.pattern}")
        normalized_id = participant_id.lower()
        if normalized_id in seen:
            raise ConfigError(f"duplicate participant id: {participant_id}")
        seen.add(normalized_id)
        plugin_id = participant.get("plugin")
        if not isinstance(plugin_id, str) or plugin_id not in registry:
            supported = ", ".join(sorted(registry)) or "(none)"
            raise ConfigError(f"{prefix}.plugin {plugin_id!r} is unknown; supported: {supported}")
        if "args" in participant and (
            not isinstance(participant["args"], list)
            or not all(isinstance(arg, str) for arg in participant["args"])
        ):
            raise ConfigError(f"{prefix}.args must be a list of strings")
        for key in ("command", "subcommand"):
            if key in participant and not isinstance(participant[key], str):
                raise ConfigError(f"{prefix}.{key} must be a string")

    settings = config.get("settings", {})
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ConfigError("settings must be a mapping")
    bounds = {
        "max_rounds": (1, 1000),
        "convergence_threshold": (1, 100),
        "stalemate_threshold": (1, 100),
        "context_window": (1, 100),
        "call_timeout": (1, 86_400),
        "retry_count": (0, 10),
    }
    for key, (minimum, maximum) in bounds.items():
        if key not in settings:
            continue
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ConfigError(f"settings.{key} must be an integer in [{minimum}, {maximum}]")
    return config


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _build_plugin(plugin_cls, p_conf: dict, settings: dict):
    kwargs = {"timeout": settings.get("call_timeout", 120)}
    for key in ("command", "subcommand", "args"):
        if key in p_conf:
            kwargs[key] = p_conf[key]
    plugin = plugin_cls(**kwargs)
    if "id" in p_conf:
        configured_id = str(p_conf["id"]).lower()
        plugin.id = configured_id
        configured_name = p_conf.get("display_name") or p_conf.get("name")
        if configured_name:
            plugin.display_name = str(configured_name)
        elif configured_id not in {"claude", "codex"}:
            plugin.display_name = configured_id.title()
    return plugin


def main():
    parser = argparse.ArgumentParser(description="AI Roundtable Discussion")
    parser.add_argument("topic", help="Discussion topic")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--max-rounds", type=int, help="Override max rounds")
    parser.add_argument("--manual", action="store_true", help="Manual moderation mode")
    parser.add_argument("--renderer", choices=["terminal", "tmux"], default=None)
    parser.add_argument("--output-dir", default="output", help="Report output directory")
    parser.add_argument("--tmux-session", default="ai-roundtable", help="tmux session name")
    parser.add_argument("--no-attach", action="store_true", help="Do not attach to tmux after creating panes")
    args = parser.parse_args()

    if not SESSION_NAME_PATTERN.fullmatch(args.tmux_session):
        parser.error("--tmux-session must contain only letters, numbers, '.', '_' or '-'")

    try:
        config = validate_config(load_config(args.config))
        settings = dict(config.get("settings") or {})
        if args.max_rounds is not None:
            if args.max_rounds < 1 or args.max_rounds > 1000:
                raise ConfigError("--max-rounds must be an integer in [1, 1000]")
            settings["max_rounds"] = args.max_rounds
        renderer_name = args.renderer or settings.get("renderer", "terminal")
        if renderer_name not in {"terminal", "tmux"}:
            raise ConfigError("settings.renderer must be 'terminal' or 'tmux'")
    except (ConfigError, RuntimeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    plugins = []
    participant_ids = []
    for p_conf in config["participants"]:
        participant_ids.append(p_conf["id"])
        plugin_cls = PLUGIN_REGISTRY[p_conf["plugin"]]
        plugins.append(_build_plugin(plugin_cls, p_conf, settings))

    terminal_renderer = TerminalRenderer()
    tmux_renderer = None
    if renderer_name == "tmux":
        tmux_renderer = TmuxRenderer(session_name=args.tmux_session, participants=participant_ids)
        tmux_renderer.start(args.topic, [p.display_name for p in plugins])
        terminal_renderer.render_header(args.topic, [p.display_name for p in plugins])
        print(f"tmux session created: {args.tmux_session}")
        print(f"attach command: {' '.join(tmux_renderer.attach_command())}")
    else:
        terminal_renderer.render_header(args.topic, [p.display_name for p in plugins])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    orch = Orchestrator(
        topic=args.topic,
        plugins=plugins,
        max_rounds=settings.get("max_rounds", 12),
        convergence_threshold=settings.get("convergence_threshold", 2),
        stalemate_threshold=settings.get("stalemate_threshold", 2),
        context_window=settings.get("context_window", 3),
        log_dir=str(output_dir),
        retry_count=settings.get("retry_count", 1),
        on_event=_make_progress_handler(tmux_renderer),
    )

    try:
        orch.start()
        if args.manual:
            _run_manual(orch, terminal_renderer)
            if orch.state == SessionState.WAITING_FOR_USER:
                wait_result = _handle_waiting(orch, terminal_renderer)
                if wait_result == "deferred":
                    print("Session deferred without final report.")
                    return
        else:
            orch.run_auto()
            if orch.state == SessionState.WAITING_FOR_USER:
                wait_result = _handle_waiting(orch, terminal_renderer)
                if wait_result == "deferred":
                    print("Session deferred without final report.")
                    return
    except RuntimeError as e:
        if tmux_renderer:
            stop_renderer = getattr(tmux_renderer, "stop", None)
            if callable(stop_renderer):
                try:
                    stop_renderer()
                except Exception:
                    pass
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    md_report, json_report = generate_report(
        topic=orch.topic,
        participants=orch.participant_names,
        end_reason=orch.end_reason,
        turns=orch.turns,
    )
    terminal_renderer.render_report(md_report)
    if tmux_renderer:
        tmux_renderer.write_room(md_report)
    _write_text_atomic(output_dir / "report.md", md_report)
    _write_text_atomic(output_dir / "report.json", json_report)
    print(f"\nReports saved to {output_dir}/")

    if tmux_renderer and not args.no_attach:
        tmux_renderer.attach()


def _handle_waiting(orch: Orchestrator, renderer: TerminalRenderer) -> str | None:
    while orch.state == SessionState.WAITING_FOR_USER:
        if orch.end_reason == "degraded":
            return _handle_waiting_for_degraded(orch, renderer)
        renderer.render_status("stalemate - awaiting input", orch.round_count, getattr(orch, "max_rounds", 0))
        try:
            user_input = input("\n[Stalemate] Enter your input (or 'end' to finish): ").strip()
        except (EOFError, KeyboardInterrupt):
            orch.finalize_user_ended()
            return "finalized"
        if user_input.lower() in END_COMMANDS:
            orch.finalize_user_ended()
            return "finalized"
        if user_input:
            orch.inject_user_input(user_input)
            orch.resume_after_user()
            orch.run_auto()
    return None


def _handle_waiting_for_degraded(orch: Orchestrator, renderer: TerminalRenderer) -> str:
    while orch.state == SessionState.WAITING_FOR_USER and orch.end_reason == "degraded":
        renderer.render_status("degraded - awaiting user decision", orch.round_count, getattr(orch, "max_rounds", 0))
        try:
            user_input = input(
                "\n[Degraded] Choose: end = finalize report, manual = staged report, wait = keep session open: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            user_input = "end"
        if user_input in {"end", "finish", "quit", "exit"}:
            orch.finalize_degraded(mode="end")
            return "finalized"
        if user_input in {"manual", "m"}:
            orch.finalize_degraded(mode="manual")
            return "finalized"
        if user_input in {"wait", "w", ""}:
            print("Session left open in degraded state. No final report generated.")
            return "deferred"
        print("Unknown option. Use end, manual, or wait.")


def _make_progress_handler(tmux_renderer: TmuxRenderer | None = None):
    def handle(event: dict) -> None:
        event_type = event["type"]
        data = event["data"]
        participant_id = data.get("plugin_id")
        messages = _format_progress_event(event_type, data)
        for message in messages:
            print(message, flush=True)
            if tmux_renderer:
                tmux_renderer.write_room(message)
                if participant_id:
                    tmux_renderer.write_participant(participant_id, message)

    return handle


def _format_progress_event(event_type: str, data: dict) -> list[str]:
    if event_type == "turn_prompted":
        return [f"[Round {data['round']}] Calling {data['plugin_id']}..."]
    if event_type == "turn_retrying":
        return [f"[Retry] {data['plugin_id']} attempt {data['attempt']} failed; retrying..."]
    if event_type == "turn_completed":
        return [f"[Done] {data['plugin_id']} returned status={data.get('status')}"]
    if event_type == "turn_failed":
        error = data.get("error") or {}
        messages = [f"[Failed] {data['plugin_id']}: {error.get('message', 'unknown error')}"]
        if error.get("detail"):
            messages.append(f"         {error['detail']}")
        return messages
    if event_type == "participant_removed":
        return [f"[Removed] {data['plugin_id']} exceeded failure threshold"]
    if event_type == "session_degraded":
        return ["[Degraded] Fewer than two participants remain."]
    if event_type == "session_completed":
        return [f"[Completed] end_reason={data.get('end_reason')}"]
    return []


def _run_manual(orch: Orchestrator, renderer: TerminalRenderer) -> None:
    known = orch.participant_ids
    while orch.state == SessionState.RUNNING:
        orch.run_one_round()
        if orch.state != SessionState.RUNNING:
            break
        try:
            user_input = input("\n[Moderator] Command (enter/continue/@name/end): ").strip()
        except (EOFError, KeyboardInterrupt):
            orch.finalize_user_ended()
            return
        if user_input.lower() in END_COMMANDS:
            orch.finalize_user_ended()
            return
        if user_input.lower() in CONTINUE_COMMANDS:
            continue
        mentions = parse_mentions(user_input, known)
        orch.inject_user_input(user_input, mentions=mentions)


if __name__ == "__main__":
    main()
