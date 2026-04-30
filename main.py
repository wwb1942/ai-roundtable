from __future__ import annotations

import argparse
import sys
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
END_COMMANDS = {"end", "exit", "quit", "结束", "退出"}
CONTINUE_COMMANDS = {"", "continue", "继续"}


def load_config(path: str) -> dict:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing dependency: PyYAML. Install with `python -m pip install -r requirements.txt`.") from exc

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_plugin(plugin_cls, p_conf: dict, settings: dict):
    kwargs = {"timeout": settings.get("call_timeout", 120)}
    for key in ("command", "subcommand", "args"):
        if key in p_conf:
            kwargs[key] = p_conf[key]
    return plugin_cls(**kwargs)


def main():
    parser = argparse.ArgumentParser(description="AI Roundtable Discussion")
    parser.add_argument("topic", help="Discussion topic")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--max-rounds", type=int, help="Override max rounds")
    parser.add_argument("--manual", action="store_true", help="Manual moderation mode")
    parser.add_argument("--renderer", choices=["terminal", "tmux"], default="terminal")
    parser.add_argument("--output-dir", default="output", help="Report output directory")
    parser.add_argument("--tmux-session", default="ai-roundtable", help="tmux session name")
    parser.add_argument("--no-attach", action="store_true", help="Do not attach to tmux after creating panes")
    args = parser.parse_args()

    config = load_config(args.config)
    settings = config.get("settings", {})
    if args.max_rounds:
        settings["max_rounds"] = args.max_rounds

    plugins = []
    participant_ids = []
    for p_conf in config.get("participants", []):
        participant_ids.append(p_conf["id"])
        plugin_cls = PLUGIN_REGISTRY.get(p_conf["plugin"])
        if plugin_cls:
            plugins.append(_build_plugin(plugin_cls, p_conf, settings))

    terminal_renderer = TerminalRenderer()
    tmux_renderer = None
    if args.renderer == "tmux":
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
        else:
            orch.run_auto()
            if orch.state == SessionState.WAITING_FOR_USER:
                wait_result = _handle_waiting(orch, terminal_renderer)
                if wait_result == "deferred":
                    print("Session deferred without final report.")
                    return
    except RuntimeError as e:
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
    (output_dir / "report.md").write_text(md_report, encoding="utf-8")
    (output_dir / "report.json").write_text(json_report, encoding="utf-8")
    print(f"\nReports saved to {output_dir}/")

    if tmux_renderer and not args.no_attach:
        tmux_renderer.attach()


def _handle_waiting(orch: Orchestrator, renderer: TerminalRenderer) -> str | None:
    while orch.state == SessionState.WAITING_FOR_USER:
        if orch.end_reason == "degraded":
            return _handle_waiting_for_degraded(orch, renderer)
        renderer.render_status("stalemate - awaiting input", orch.round_count, 0)
        try:
            user_input = input("\n[Stalemate] Enter your input (or 'end' to finish): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
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
        renderer.render_status("degraded - awaiting user decision", orch.round_count, 0)
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
            break
        if user_input.lower() in END_COMMANDS:
            break
        if user_input.lower() in CONTINUE_COMMANDS:
            continue
        mentions = parse_mentions(user_input, known)
        orch.inject_user_input(user_input, mentions=mentions)


if __name__ == "__main__":
    main()
