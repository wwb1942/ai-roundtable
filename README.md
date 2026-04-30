# AI Roundtable

AI Roundtable is a local multi-agent discussion tool. It can place Claude,
Codex, and other configured participants into one tmux session, exchange
visible messages through a shared MCP room store, and let a broker coordinate
message routing, reply waits, fallbacks, and debate turns.

## Architecture

```text
+-------------------------------------------------------------+
|                          tmux UI                            |
|  left: room pane                 right: agent CLI panes      |
|  you: <moderator>                claude / codex / others     |
|  claude> ...                                                 |
|  codex> ...                                                  |
+---------------^-------------------------------+-------------+
                |                               |
                | clean chat/status output       | prompts + fallback capture
                |                               |
+---------------+-------------------------------v-------------+
|                        room broker                          |
|  parses @mentions, /debate, /verbose, /sync, /help           |
|  posts user events, sends agent prompts, waits for replies   |
|  filters prompt echo and terminal fallback noise             |
+---------------^-------------------------------+-------------+
                |                               |
                | JSONL room events             | MCP tools
                |                               |
+---------------+-------------------------------v-------------+
|                          MCP room                           |
|  room_read / room_post / room_state backed by JSONL storage  |
+-------------------------------------------------------------+
```

## Quick Start

Run from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-tmux-roundtable.ps1
```

The launcher creates `.venv`, installs dependencies, generates MCP config,
starts a WSL tmux session, opens the room pane on the left, and opens agent CLI
panes on the right.

## Configuration

The main configuration file is `config.yaml`:

- `participants`: list of roundtable participants.
- `participants[].id`: participant id and room chat prefix, such as `claude>`.
- `participants[].plugin`: plugin used by batch/orchestrated mode.
- `participants[].command`: command used by batch/orchestrated mode.
- `participants[].subcommand`: optional subcommand, such as Codex `exec`.
- `participants[].args`: command arguments.
- `moderator.name`: moderator name shown as `you: <name>` in the room pane.
- `settings.max_rounds`: maximum orchestrated rounds.
- `settings.convergence_threshold`: consecutive convergence threshold.
- `settings.stalemate_threshold`: stalemate detection threshold.
- `settings.context_window`: recent context window passed to participants.
- `settings.call_timeout`: per-plugin call timeout in seconds.
- `settings.renderer`: renderer for batch mode, usually `terminal` or `tmux`.
- `settings.output_mode`: output mode, such as `report`.
- `summary.strategy`: report summary strategy.
- `summary.participant_id`: optional participant selected for summary.

## Commands

The room broker supports these commands:

- `@claude <message>`: send a message to Claude.
- `@codex <message>`: send a message to Codex.
- `@all <message>`: broadcast a message to all configured participants.
- `/debate [rounds] <topic>`: run a multi-round debate, default 3 rounds, max 10.
- `@all /debate [rounds] <topic>`: targeted debate form for all participants.
- `/verbose on`: show routing, waiting, fallback, and diagnostic messages.
- `/verbose off`: hide diagnostics and keep only the clean chat stream.
- `/help`: print the full command summary.
- `/state`: show room event count, message count, and participants.
- `/tail [N]`: show the last N room messages.
- `/sync [@target|@all] [lines]`: capture recent agent pane output for fallback debugging.
- `/quit`: exit the broker.

## Known Limitations

- Interactive tmux mode depends on WSL and tmux. If Windows policy blocks WSL
  startup, the launcher cannot create the session.
- Claude, Codex, and other agent CLIs must already be installed and logged in on
  the local machine.
- MCP config writes to `~/.codex/config.toml` can be blocked by file permissions
  or another running Codex process.
- Third-party API proxies may filter prompts or return policy errors; use the
  official endpoint or a reliable proxy when that happens.
- Terminal fallback can only recover visible, stable final output from an agent
  pane. The preferred path is still MCP `room_post`.
