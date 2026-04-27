from __future__ import annotations
import argparse
import sys
import yaml
from pathlib import Path
from src.orchestrator import Orchestrator
from src.plugins.claude_plugin import ClaudePlugin
from src.plugins.codex_plugin import CodexPlugin
from src.renderers.terminal import TerminalRenderer
from src.report import generate_report
from src.mention import parse_mentions
from src.models import SessionState

PLUGIN_REGISTRY = {"claude": ClaudePlugin, "codex": CodexPlugin}

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def main():
    parser = argparse.ArgumentParser(description="AI Roundtable Discussion")
    parser.add_argument("topic", help="Discussion topic")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--max-rounds", type=int, help="Override max rounds")
    parser.add_argument("--manual", action="store_true", help="Manual moderation mode")
    parser.add_argument("--renderer", choices=["terminal", "tmux"], default="terminal")
    parser.add_argument("--output-dir", default="output", help="Report output directory")
    args = parser.parse_args()

    config = load_config(args.config)
    settings = config.get("settings", {})
    if args.max_rounds:
        settings["max_rounds"] = args.max_rounds

    plugins = []
    for p_conf in config.get("participants", []):
        plugin_cls = PLUGIN_REGISTRY.get(p_conf["plugin"])
        if plugin_cls:
            plugins.append(plugin_cls(timeout=settings.get("call_timeout", 120)))

    renderer = TerminalRenderer()
    renderer.render_header(args.topic, [p.display_name for p in plugins])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    orch = Orchestrator(
        topic=args.topic, plugins=plugins,
        max_rounds=settings.get("max_rounds", 12),
        convergence_threshold=settings.get("convergence_threshold", 2),
        stalemate_threshold=settings.get("stalemate_threshold", 2),
        context_window=settings.get("context_window", 3),
        log_dir=str(output_dir),
    )

    try:
        orch.start()
        if args.manual:
            _run_manual(orch, renderer)
        else:
            orch.run_auto()
            if orch.state == SessionState.WAITING_FOR_USER:
                _handle_waiting(orch, renderer)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    md_report, json_report = generate_report(
        topic=orch.topic,
        participants=orch.participant_names,
        end_reason=orch.end_reason,
        turns=orch.turns,
    )
    renderer.render_report(md_report)
    (output_dir / "report.md").write_text(md_report, encoding="utf-8")
    (output_dir / "report.json").write_text(json_report, encoding="utf-8")
    print(f"\nReports saved to {output_dir}/")

def _handle_waiting(orch: Orchestrator, renderer: TerminalRenderer) -> None:
    while orch.state == SessionState.WAITING_FOR_USER:
        renderer.render_status("stalemate - awaiting input", orch.round_count, 0)
        try:
            user_input = input("\n[Stalemate] Enter your input (or 'end' to finish): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in ("end", "exit", "quit", "结束", "退出"):
            break
        if user_input:
            orch.inject_user_input(user_input)
            orch.resume_after_user()
            orch.run_auto()

def _run_manual(orch: Orchestrator, renderer: TerminalRenderer) -> None:
    known = [p.id for p in orch._plugins]
    while orch.state == SessionState.RUNNING:
        orch._run_one_round()
        if orch.state != SessionState.RUNNING:
            break
        try:
            user_input = input("\n[Moderator] Command (enter/continue/@name/end): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in ("end", "exit", "quit", "结束", "退出"):
            break
        if user_input.lower() in ("", "continue", "继续"):
            continue
        mentions = parse_mentions(user_input, known)
        orch.inject_user_input(user_input)

if __name__ == "__main__":
    main()
