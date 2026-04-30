# MCP Room Tool

The tmux panes are now only the visual shell. Shared roundtable messages should flow through the local MCP room server.

## Server Command

From the project root:

```powershell
.\.venv\Scripts\python.exe scripts\room_mcp_server.py --room-file D:\projects\ai-roundtable-codex\logs\mcp-room-ai-roundtable.jsonl
```

The launcher prints the exact command for the active session.

## Tools

- `room_read`: read recent room events.
- `room_post`: post a final visible reply to the shared room.
- `room_state`: inspect room state.

## Agent Instruction

Each agent should read the room with `room_read` and publish only its final visible answer with `room_post`.
The room pane displays `room_post` messages. It does not scrape thinking logs from the agent panes.

The broker sends only ASCII text into tmux participant panes. The real user message is stored in the UTF-8 room file and referenced by event id. If `room_read` fails, the prompt includes an ASCII UTF-8 base64 fallback copy of the same message.

Agents must not use shell commands, Python scripts, PowerShell, or direct file writes to post room replies. Replies must go through MCP `room_post`; if that fails, use the terminal fallback below. Terminal fallback payloads should use the base64 format so non-ASCII replies do not pass through tmux directly.

## Room Commands

On startup the room pane is intentionally quiet: it prints `you: <moderator name>`, a single topic/participants comment, then visible chat lines in the form `claude> ...`, `codex> ...`, and `you> ...`.
Internal routing diagnostics are hidden by default.

- `/state`: print room event/message counts, participants, and last event id.
- `/tail [N]`: print the last N room messages, defaulting to 10.
- `/sync [@claude|@codex|@all] [lines]`: manually capture participant panes for debugging.
- `/debate [rounds] <topic>`: run an automatic multi-round debate between Claude and Codex, defaulting to 3 rounds and capped at 10. The broker sends one participant at a time and waits for the reply before prompting the next participant, so each side can respond to the prior point.
- `/verbose on`: show routing diagnostics such as sent targets, debate turn markers, reply framing, and waiting heartbeats.
- `/verbose off`: hide routing diagnostics again.
- `@all /debate [rounds] <topic>`: equivalent targeted form; useful if more participants are added later.
- `@all <message>`: broadcast `<message>` to every participant for a single round of replies. Use `/debate` (or `@all /debate`) explicitly when you want a multi-round debate.

The broker startup banner is one line: `room ready. type /help for commands.` Use `/help` to print the full command summary.
The broker posts a system session marker on startup so fresh runs can be distinguished from older append-only room history.
While waiting for replies the broker has no automatic timeout. Press `Ctrl+C` during the wait to cancel that wait and return to the `room>` prompt without stopping the tmux session.

## Fallback

If an agent can read the room but `room_post` fails, it should print:

```text
ROOM_POST_FALLBACK_BASE64_BEGIN
<utf8-base64 final visible reply>
ROOM_POST_FALLBACK_BASE64_END
```

Legacy plain-text fallback is still accepted:

```text
ROOM_POST_FALLBACK_BEGIN
<final visible reply>
ROOM_POST_FALLBACK_END
```

The broker captures that marked terminal fallback, writes it back to the room store, and displays it in the room pane. `/sync` still exists as a manual debug capture command.
