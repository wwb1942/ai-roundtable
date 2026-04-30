# AI Roundtable — Clean Room UI Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the room pane look like a clean, single-stream chat: each line prefixed with `you> ` / `claude> ` / `codex> ` (and any other configured participant id). Hide internal orchestration noise (`[debate] round X`, `[waiting] still waiting for: X`, `--- claude reply ---`, `sent -> Y`) by default. Show a one-line `you: <moderator name>` header at the top of the room. Keep the right-side agent panes untouched (they are the agent's own TUI).

**Reference image:** see `企业微信截图_<date>.png` for visual target — `you: Alice` header, conversation lines like `claude>` / `codex>` / `you>`, no debate metadata in the room pane.

**Scope boundary:** This plan does NOT change the orchestrator state machine, plugin code, MCP server, report generation, or anything in `src/orchestrator.py` / `src/plugins/` / `src/report.py` / `scripts/room_mcp_server.py`. All changes live in:
- `scripts/room_broker.py` (UI text + prompt-echo filter)
- `src/renderers/tmux.py` (room pane header)
- `scripts/start_interactive_tmux.py` (pass moderator name + participants through)
- `config.yaml` (new `moderator.name` field)
- `tests/` (new + updated tests)

**Tech Stack:** Same as base project (Python ≥ 3.9, PyYAML, stdlib).

---

## Problem Analysis

Current room pane prints, in chronological order:

1. `Room broker ready.` + a usage banner listing every command
2. Every user input is followed by `sent -> <targets>`
3. Each agent turn frames its reply with `--- <agent> reply ---` ... `--- end <agent> ---`
4. Debate adds `[debate] round X/Y turn N/M sent -> X`
5. Waiting for replies prints `[waiting] still waiting for: X` every 30s
6. Terminal-fallback captures sometimes leak prompt-echo lines into room (e.g. `Read the shared roundtable room via the MCP tool room_read.`, `Target room event id: ...`, `If room_read fails, ...`)
7. No identity for the moderator — the user types blind

The reference UI (other team's implementation) shows:

- One header line: `you: <moderator name>`
- Body is just `<role>> <text>` per line, no separators
- All status/debug noise is suppressed unless verbose mode is on

**Decisions confirmed with user:**

- **D1: Status noise** — Hide by default. Add a `/verbose on` and `/verbose off` slash command + `--verbose` CLI flag. When verbose is OFF: suppress `sent ->`, `[debate] round`, `[waiting] still waiting`, `--- agent reply ---` framing. When verbose is ON: print all current diagnostics. Errors and timeouts (`[timeout]`, `[failed]`, `[error]`) are ALWAYS printed regardless of verbose.
- **D2: Moderator name** — read from `config.yaml`'s new `moderator.name` field (default `"You"` if missing). Top of room pane prints `you: <name>` at startup.
- **D3: Prompt-echo filter** — keep existing sliding-window/skip_prefixes logic. Additionally: `RoomBroker.send` records the exact prompt text it just sent to a target; when capturing terminal fallback for that target, strip any prefix matching that recorded prompt before further extraction. This makes filtering robust without depending on a keyword blacklist.

---

## Target File Structure

No new files. All changes are edits to existing files:

```text
config.yaml                          # add moderator.name
scripts/room_broker.py               # quiet by default, verbose toggle, prompt-echo filter
src/renderers/tmux.py                # accept moderator_name, write 'you: <name>' on startup
scripts/start_interactive_tmux.py    # load moderator.name from config, pass through
tests/test_room_broker.py            # cover quiet/verbose, prompt-echo filter
tests/test_tmux_renderer.py          # cover moderator header
tests/test_start_interactive_tmux.py # cover moderator wiring
docs/mcp-room.md                     # document the new UX rules
```

---

## Implementation Plan

### Phase 1 — Suppress chat-flow noise (room_broker.py)

#### Task 1.1 — Add a `verbose` flag to `RoomBroker`

- [ ] In `scripts/room_broker.py`, add `verbose: bool = False` to `RoomBroker.__init__` parameters; store as `self.verbose`.
- [ ] Add a method `RoomBroker.set_verbose(value: bool)` that flips `self.verbose` and prints `"[verbose] on"` or `"[verbose] off"`.
- [ ] In `main()` (the CLI bottom), add an argparse flag `--verbose` that initializes the broker with `verbose=True`.

**Acceptance:** new flag tested by constructing `RoomBroker(verbose=True)` and asserting `broker.verbose is True`. `set_verbose(False)` flips it back.

---

#### Task 1.2 — Funnel all status messages through a single `_say` helper

- [ ] In `RoomBroker`, add `_say_status(self, text: str) -> None` and `_say_error(self, text: str) -> None`. The status one prints only when `self.verbose`; the error one always prints.
- [ ] Replace these existing `print(...)` call sites in `scripts/room_broker.py` with `self._say_status(...)` (or move the call into a method on the broker, then route through `_say_status`):
  - `print(f"sent -> {', '.join(targets)}")` (in `main`'s loop)
  - `print(f"[debate] round {round_number}/{safe_rounds} turn {turn_index}/{len(round_targets)} sent -> {target}")` (in `run_debate`)
  - `print(f"[debate] @all defaults to {DEFAULT_DEBATE_ROUNDS} debate rounds.")` (Codex already neutered `should_auto_debate`, but this print may still trigger from other paths — search and silence)
  - `print(f"\n--- {author_key} reply ---")` and `printer(f"--- end {author_key} ---")` and `printer(f"--- {target} terminal fallback ---")` (in `wait_for_room_replies`)
  - `print(f"\n[waiting] still waiting for: {missing}")` (heartbeat)
  - `print(f"[input] no message supplied; asking ...")` (continuation hint)
  - `print(f"Use @{', @'.join(broker.targets)}, ...")` (parser hint when no target — keep this one as `_say_error` since it's a usage error)
- [ ] Errors keep using `_say_error`:
  - `print(f"\n[timeout] timed out waiting for: {missing}")`
  - `print(f"\n[cancelled] wait interrupted; returning to room prompt.")` — keep as status (it's expected on Ctrl+C; route through `_say_status`)
- [ ] Pass `printer=self._say_status` to `wait_for_room_replies` and `run_debate` instead of the default `print`. The internal heartbeat/turn-marker prints inside those functions then automatically respect verbose.

**Acceptance:** new test `test_quiet_mode_suppresses_status_noise` constructs a broker with `verbose=False`, captures `_say_status` output via dependency injection, runs a fake debate round, and asserts the captured stream is empty. `test_verbose_mode_shows_status_noise` does the inverse: constructs with `verbose=True` and asserts the heartbeat / turn lines appear.

---

#### Task 1.3 — Add `/verbose on` / `/verbose off` slash command

- [ ] In `main()`'s input loop, when `text` matches `/verbose on` or `/verbose off`, call `broker.set_verbose(True/False)` and `continue`.
- [ ] Add unit test: simulate `text == "/verbose on"`, then capture stdout, assert `"[verbose] on"` printed and `broker.verbose is True`.

**Acceptance:** test passes. Manual flow: type `/verbose on` in the room → next debate prints turn markers; `/verbose off` → next debate is silent.

---

### Phase 2 — Quiet usage banner + moderator header (tmux.py + room_broker.py + config.yaml)

#### Task 2.1 — Add `moderator.name` to `config.yaml`

- [ ] Edit `config.yaml`. Add at the top level (alongside `participants:` and `settings:`):
  ```yaml
  moderator:
    name: You
  ```
- [ ] Update `scripts/start_interactive_tmux.py`'s `load_participant_ids` style: add a sibling helper `load_moderator_name(config_path: Path) -> str` returning `config["moderator"]["name"]` if present, else `"You"`. Default also when YAML / file missing (same fallback pattern).

**Acceptance:** unit test for `load_moderator_name` covers: present field, missing `moderator` block, missing config file.

---

#### Task 2.2 — `TmuxRenderer` accepts `moderator_name`, writes header

- [ ] Add parameter `moderator_name: str = "You"` to `TmuxRenderer.__init__`. Store as `self._moderator_name`.
- [ ] In `start()` and `start_interactive()`, **replace** the current room-pane preamble:
  ```python
  self.write_room(f"Topic: {topic}")
  self.write_room(f"Participants: {', '.join(participants)}")
  ```
  with:
  ```python
  self.write_room(f"you: {self._moderator_name}")
  self.write_room(f"# {topic}  (participants: {', '.join(participants)})")
  ```
  The first line is the `you: <name>` identity badge from the reference image. The second is a single comment line so the topic + roster aren't lost — but visually subordinate.
- [ ] In `start()`, REMOVE these lines (they pollute the room pane with chat noise):
  ```python
  self.write_participant("claude", "Claude pane ready.")
  self.write_participant("codex", "Codex pane ready.")
  ```
  (Per-participant pane readiness is obvious from the pane being created. The room pane shouldn't echo it.)
- [ ] In `start_interactive()`, REMOVE:
  ```python
  self.write_room("Interactive mode: right panes run the real Claude Code and Codex CLIs.")
  self.write_room("Type in the room pane, or use the broker script to send prompts to agent panes.")
  ```
  These messages are obvious from context and add visual noise.

**Acceptance:** new test in `tests/test_tmux_renderer.py`: assert `start()` calls `write_room` with `"you: Alice"` first when `moderator_name="Alice"`, then once more with the topic/participant comment. Existing tests update their fixtures to pass `moderator_name="You"` (default).

---

#### Task 2.3 — Quiet broker startup banner

- [ ] In `scripts/room_broker.py`'s `main()`, REPLACE:
  ```python
  print("Room broker ready.")
  print("Usage: @claude message | @codex message | @all message | /debate [rounds] topic | /state | /sync [@claude|@codex|@all] [lines] | /quit")
  ```
  with:
  ```python
  print(f"room ready. type /help for commands.")
  ```
- [ ] Add a `/help` slash command in the input loop that prints the full usage line. Same wording as before but only on demand.

**Acceptance:** unit test (or manual run): start broker → see only `"room ready. type /help for commands."`. Type `/help` → full usage prints.

---

#### Task 2.4 — Wire `moderator_name` from launcher → tmux renderer

- [ ] In `scripts/start_interactive_tmux.py`'s `main()`, after loading participant ids, also load `moderator_name = load_moderator_name(Path(args.config))`.
- [ ] Pass `moderator_name=moderator_name` to `TmuxRenderer(...)` constructor.
- [ ] Update `tests/test_start_interactive_tmux.py` — assert that when `config.yaml` has `moderator: { name: Alice }`, `TmuxRenderer.__init__` is called with `moderator_name="Alice"`. When `moderator` block missing, call uses `moderator_name="You"`.

**Acceptance:** new tests pass. Existing 2-participant test still passes with default.

---

### Phase 3 — Prompt-echo filter robustness (room_broker.py)

#### Task 3.1 — `RoomBroker.send` records last prompt per target

- [ ] In `RoomBroker.__init__`, add `self._last_prompt_by_target: dict[str, str] = {}`.
- [ ] In `send(target, message, event_id)`, after building the prompt via `format_agent_prompt(message, event_id=event_id)`, store it: `self._last_prompt_by_target[target] = prompt`.
- [ ] Expose a getter `RoomBroker.last_prompt_for(target: str) -> str | None`.

**Acceptance:** unit test sends two messages to `claude`, asserts `broker.last_prompt_for("claude")` returns the LATEST prompt (the second one). Asserts `last_prompt_for("never-sent")` returns `None`.

---

#### Task 3.2 — Strip the recorded prompt prefix from terminal fallback captures

- [ ] In `RoomBroker.terminal_fallback_replies`, after computing `changed = output[len(baseline):]` for each target, ALSO strip the recorded prompt:
  ```python
  last_prompt = self._last_prompt_by_target.get(target)
  if last_prompt:
      # Find the prompt anywhere in `changed` and drop everything up to + including it.
      # This is robust even if the terminal wraps lines or inserts ANSI codes between
      # baseline and the prompt echo.
      idx = changed.find(last_prompt[:80])  # match first 80 chars; full match is fragile due to wrapping
      if idx != -1:
          changed = changed[idx + len(last_prompt[:80]):]
  ```
- [ ] Continue with the existing `extract_room_replies` / `output_is_noise` / `extract_visible_terminal_reply` flow on the now-stripped `changed`.

**Acceptance:** new test `test_terminal_fallback_strips_recorded_prompt_echo`: construct broker, call `send("claude", "what's the topic?")` (which records the prompt), then feed a synthesized terminal capture that contains the full prompt followed by `"\n\nThe topic is unclear.\n"` to `terminal_fallback_replies`. Assert the recovered reply is exactly `"The topic is unclear."` — no prompt template fragments.

---

### Phase 4 — Verification

#### Task 4.1 — Full automated test suite

- [ ] Run `python -m pytest tests/ -v`. Expect prior count + new tests added in tasks above. Zero failures.
- [ ] Confirm by grep that the room pane no longer prints any of:
  - `sent -> ` (outside verbose path)
  - `[debate] round` (outside verbose path)
  - `[waiting] still waiting` (outside verbose path)
  - `--- ... reply ---` (outside verbose path)
  - `Claude pane ready` / `Codex pane ready` (anywhere)
  - `Interactive mode: right panes run` (anywhere)

  Use:
  ```bash
  grep -n "sent -> \|\[debate\] round\|\[waiting\] still waiting\|--- .* reply ---" scripts/room_broker.py
  ```
  All hits should be inside a method that respects `self.verbose`.

#### Task 4.2 — Manual smoke

- [ ] Run `.\scripts\start-tmux-roundtable.ps1` with default config (moderator name = "You")
- [ ] In room pane: top should show `you: You` then the topic/participants comment line
- [ ] Type `@all hello` → expect ONLY two lines per agent: `claude> ...` and `codex> ...`. No `sent ->`, no `[waiting]`, no `--- ... reply ---` framing.
- [ ] Type `/debate 1 vibe coding` → expect first `claude> <opening analysis>`, then `codex> <response>`. No turn markers in between.
- [ ] Type `/verbose on` → next round shows the original diagnostic prints.
- [ ] Type `/verbose off` → diagnostics gone again.
- [ ] Edit `config.yaml`, set `moderator: { name: MickWang }`, restart → top header reads `you: MickWang`.

**Acceptance:** all checked.

---

## Notes for the Implementer (Codex)

1. **Do not change any prompt strings sent to agents.** `format_agent_prompt` and `format_debate_turn_message` stay byte-for-byte the same. We're only changing what the BROKER prints to its OWN room pane.

2. **Errors must always print.** `[timeout]`, hard exceptions, and validation errors stay visible in quiet mode. The point is to hide success-path noise, not to hide failures.

3. **Don't reformat unrelated code.** This plan touches specific lines in 4 files. If a refactor opportunity arises beyond what's listed, leave a TODO comment and move on.

4. **Test count never shrinks.** If you delete a test, justify it explicitly. Prefer adding tests for new behaviors.

5. **Do not touch:** `src/orchestrator.py`, `src/plugins/`, `src/report.py`, `scripts/room_mcp_server.py`, `src/event_log.py`, `src/context_manager.py`. Reviewer will reject diffs that bleed into these files.

6. **If a task feels under-specified or conflicts with current code (like Phase 1 of the previous plan with `finalize_user_ended`), STOP and ask.** Don't guess.

---

## Review Checklist (for the reviewer)

- [ ] All 11 tasks marked `- [x]`
- [ ] `python -m pytest tests/` shows 169+ passing, 0 failing
- [ ] `git diff --stat` confined to the 7 files in "Target File Structure"
- [ ] No diff in `src/orchestrator.py` / `src/plugins/` / `src/report.py` / `scripts/room_mcp_server.py`
- [ ] Quiet-mode smoke: `@all hello` produces `claude>` and `codex>` lines only
- [ ] Verbose-mode smoke: same input produces the original diagnostics
- [ ] Header smoke: `config.yaml` `moderator.name` flows through to the `you: <name>` line at top of room pane
- [ ] Prompt-echo smoke: trigger a terminal fallback (e.g. by quickly Ctrl+C-ing an MCP request) → recovered reply contains no prompt template fragments
