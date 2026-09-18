# AI Roundtable

[![CI](https://github.com/wwb1942/ai-roundtable/actions/workflows/ci.yml/badge.svg)](https://github.com/wwb1942/ai-roundtable/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/wwb1942/ai-roundtable/graph/badge.svg)](https://codecov.io/gh/wwb1942/ai-roundtable)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

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

The interactive launcher keeps Codex's approval and sandbox protections enabled
by default. Passing `--allow-unsafe-codex` explicitly disables those protections
and should only be used in an isolated, trusted workspace. Custom participant
IDs must provide an explicit `command` in `config.yaml`; unknown IDs are not
treated as executable names automatically.

## Batch Workflows

The original free-form roundtable remains available in both forms:

```powershell
python main.py "Should this service use an event queue?"
python main.py discuss "Should this service use an event queue?"
```

Roundtable Maintainer applies the same multi-agent deliberation to a local
Python Git repository. It creates a separate Git worktree, runs a diagnostic
roundtable, lets one selected participant implement the change, executes
deterministic checks, and finishes with a review roundtable:

```powershell
python main.py maintain `
  --repo E:\projects\example `
  --issue "Fix the failing retry test" `
  --check "python -m pytest"
```

Use `--issue-file issue.md` for longer issue descriptions and repeat `--check`
to run more than one command. The default executor is `codex` when configured;
override it with `--executor PARTICIPANT_ID`. Checks are launched as argument
arrays without a command shell. If no check is supplied for a detected Python
project, the workflow runs the current interpreter with `-m pytest`.

The source repository must be clean by default. `--allow-dirty` permits a run,
but uncommitted source changes are not copied into the isolated worktree. The
workflow never commits, pushes, opens a pull request, merges, or removes its
worktree. A successful run ends in `awaiting_approval`; any failed check,
participant error, unexpected Git-state change, reviewer mutation, or lack of
review consensus ends in `needs_attention`. See [docs/maintainer.md](docs/maintainer.md)
for the lifecycle, artifacts, and cleanup commands.

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

Configuration is validated before tmux panes or participant sessions are
created. Participant IDs must be unique, plugin names must be registered, and
numeric settings are bounded to prevent accidental runaway sessions.
When a non-default participant alias is configured, the launcher leaves the
MCP author field flexible so that the alias can identify itself; keep such
rooms on a trusted local machine.

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

Room events include session and request correlation IDs. Participant replies
must reference the request they answer, which prevents delayed replies from a
previous prompt being accepted as the current turn. Room reads are bounded and
malformed trailing JSONL records are skipped with a warning.

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

## License

MIT - see [LICENSE](LICENSE) for the full text.
