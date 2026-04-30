# AI Roundtable — Tech Debt Cleanup & Dynamic Participants Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove three documented tech-debt items that have accumulated over the past review cycles, then refactor the participant set from hard-coded `["claude", "codex"]` into a config-driven list. After this plan, adding a third agent (e.g. Gemini, GPT-4o) requires editing only `config.yaml`, not source code.

**Scope boundary:** This plan does NOT introduce new features (e.g. token tracking, replay tooling, new orchestration strategies). Those will be follow-up plans. Keep diffs focused — reviewer is checking that nothing else regresses.

**Tech Stack:** Same as base project (Python ≥ 3.9, PyYAML, stdlib).

---

## Problem Analysis

### Part A — Tech debt (3 items)

These were flagged in earlier review rounds but never landed because they were classified as "non-blocking for V1":

1. **`main.py:198-200`** has a function `_handle_degraded` that is never called from anywhere. Dead code.
2. **`main.py:130-131`** stalemate→user-end path: when the user types `end` at the stalemate prompt, the loop `break`s without setting `orch.end_reason`. Downstream `report.py:15` then computes `confidence = "low"` and the markdown output reads `**End Reason:** None`. The spec §11.2 enum reserves `user_ended` for this case.
3. **`src/plugins/claude_plugin.py:128`** — `_detect_protocol_violation(content, status_data)` declares `status_data` but never uses it. Either drop the parameter or wire it up. Drop.

### Part B — Hard-coded participant identifiers

The participant set `["claude", "codex"]` is hard-coded in at least 4 locations:

- `scripts/room_broker.py` — `KNOWN_TARGETS = ["claude", "codex"]` (top-level constant)
- `scripts/room_broker.py` — `RoomBroker.__init__` builds `self.panes = {"claude": "...:0.1", "codex": "...:0.2"}`
- `src/renderers/tmux.py` — `TmuxRenderer.__init__` builds `self._panes = {"room": ..., "claude": ..., "codex": ...}`
- `src/renderers/tmux.py` — `start_interactive` hard-codes `claude_command` / `codex_command` parameters; `start` writes `"Claude pane ready."` / `"Codex pane ready."`

The `config.yaml` already has a `participants:` list with each entry's `id`, `plugin`, `command`. We just don't propagate that list to broker/renderer. After this refactor, adding a third participant should be a `config.yaml` edit only.

**Out of scope for this plan:**
- Changing the orchestrator's plugin loading (it's already config-driven via `PLUGIN_REGISTRY`)
- Adding new plugin types (Gemini, GPT, etc.) — that's a separate task
- Changing the tmux pane layout algorithm beyond what's needed to support N panes

---

## Target File Structure

No new files. All changes are edits to existing files:

```text
main.py                          # remove dead function, set end_reason
src/plugins/claude_plugin.py     # drop unused parameter
src/renderers/tmux.py            # accept participant list, build panes dynamically
scripts/room_broker.py           # accept targets from caller, derive panes from list
scripts/start_interactive_tmux.py # pass participant list to TmuxRenderer + room_broker
tests/test_main.py                # cover end_reason="user_ended" case
tests/test_tmux_renderer.py       # cover N-participant pane layout
tests/test_room_broker.py         # cover dynamic targets list
```

---

## Implementation Plan

### Phase 1 — Tech debt cleanup (small, isolated)

#### Task 1.1 — Remove dead function `_handle_degraded` in `main.py`

- [ ] Delete `_handle_degraded(orch, renderer)` (currently `main.py:198-200`)
- [ ] Confirm via grep that nothing references it: `grep -rn "_handle_degraded" --include="*.py"` should return only the definition line (which you're removing) — no other hits
- [ ] Run full test suite to ensure no breakage

**Acceptance:** `grep -rn "_handle_degraded" --include="*.py"` returns nothing after the change. All existing tests still pass.

---

#### Task 1.2 — Set `end_reason="user_ended"` on stalemate→end path

- [ ] In `main.py:_handle_waiting`, when the user input matches `END_COMMANDS`, set `orch.end_reason = "user_ended"` BEFORE the `break`
- [ ] Add `"user_ended"` to the documented end_reason enum in `src/models.py` if there is a Literal type for it; if not, leave as a free-form string but add it to the spec §11.2 enum comment block
- [ ] Add a test in `tests/test_main.py` (the file exists already): simulate a stalemate, feed `"end"` as input, assert `orch.end_reason == "user_ended"` AND assert the generated markdown report contains `**End Reason:** user_ended`
- [ ] Verify `report.py:_render_markdown` and `_extract_conclusion` handle `user_ended` gracefully (likely already do — they only special-case `"degraded"`)

**Acceptance:** new test passes. Existing tests pass. Manual run with `--max-rounds 2` and forcing stalemate produces a report whose `End Reason` is `user_ended`, not `None`.

---

#### Task 1.3 — Drop unused `status_data` parameter from `_detect_protocol_violation`

- [ ] In `src/plugins/claude_plugin.py`, change `_detect_protocol_violation(content: str, status_data: dict | None)` to `_detect_protocol_violation(content: str)`
- [ ] Update the call site at `claude_plugin.py:51` (search for `_detect_protocol_violation(content, status_data)`) to pass only `content`
- [ ] If the test `tests/test_claude_plugin.py` calls this helper directly, update those calls too
- [ ] Run `pytest tests/test_claude_plugin.py` — all green

**Acceptance:** function signature is `_detect_protocol_violation(content: str) -> dict | None`. Tests pass.

---

### Phase 2 — Dynamic participants

#### Task 2.1 — `TmuxRenderer` accepts a participant list

- [ ] Change `TmuxRenderer.__init__` signature: add a new required parameter `participants: list[str]` (list of participant ids in display order). Keep `session_name` as before.
- [ ] Build `self._panes` dynamically:
  ```python
  self._panes = {"room": f"{session_name}:0.0"}
  for index, pid in enumerate(participants, start=1):
      self._panes[pid.lower()] = f"{session_name}:0.{index}"
  ```
- [ ] In `start()`, replace the hard-coded two `split-window` calls with a loop over `participants`:
  - First participant: `split-window -h -p 50 -t {session}:0`
  - Subsequent participants: `split-window -v -p 50 -t {session}:0.{prev_index}`
  - This keeps backward-compat for the 2-participant default; for 3+ it tiles vertically on the right.
- [ ] Same loop pattern in `start_interactive()` — replace hard-coded `claude_split` / `codex_split` with iteration. Each participant's command comes from a new dict parameter `participant_commands: dict[str, str]` (e.g. `{"claude": "claude", "codex": "codex --foo"}`).
- [ ] In `start()`, replace `self.write_participant("claude", "Claude pane ready.")` with a loop that says `f"{pid.title()} pane ready."` for each id.
- [ ] Update `tests/test_tmux_renderer.py`:
  - Existing tests still work by passing `participants=["claude", "codex"]`
  - Add a new test `test_start_creates_n_pane_layout_for_three_participants` that passes `participants=["claude", "codex", "gemini"]` and asserts 3 split-window calls + 4 select-pane calls (room + 3 agents)

**Acceptance:** existing tmux tests pass with explicit `participants=["claude","codex"]` arg. New 3-participant test passes. No more string literal `"claude"` or `"codex"` in `tmux.py` outside of the default-args fallback.

---

#### Task 2.2 — `RoomBroker` accepts a targets list, removes `KNOWN_TARGETS` constant

- [ ] In `scripts/room_broker.py`, change `RoomBroker.__init__` signature: add a new required parameter `targets: list[str]` (list of participant ids). Keep all existing parameters.
- [ ] Build `self.panes` dynamically from `targets`:
  ```python
  self.panes = {pid.lower(): f"{session}:0.{index+1}" for index, pid in enumerate(targets)}
  ```
- [ ] Replace `KNOWN_TARGETS = ["claude", "codex"]` at module top: keep it as a default fallback for `parse_sync_command` / `parse_debate_command` style code paths, but mark it clearly:
  ```python
  DEFAULT_KNOWN_TARGETS = ["claude", "codex"]  # fallback when no broker targets passed
  ```
- [ ] Anywhere `KNOWN_TARGETS` was used to drive routing decisions, prefer `broker.targets` (a new property exposing the list); keep the module-level constant only for parsing user commands like `@all` / `@claude`.
- [ ] In `run_debate(broker, topic, rounds, targets=None)`: when `targets` is None, use `broker.targets` (not the global `KNOWN_TARGETS`).
- [ ] In `should_auto_debate` and `parse_target_message`, when matching `@<name>` mentions, validate against `broker.targets` instead of the module-level constant.
- [ ] Update `tests/test_room_broker.py`: existing tests should pass `targets=["claude", "codex"]` explicitly. Add at least one test with `targets=["claude", "codex", "gemini"]` exercising `run_debate` and verifying the turn order matches `[["claude"], ["codex"], ["gemini"], ["claude"], ["codex"], ["gemini"]]` for a 2-round debate.

**Acceptance:** `KNOWN_TARGETS` is no longer used as a routing source — only as a parser default. Tests with 3 participants pass. Existing 2-participant tests still pass after the explicit `targets=` argument is added.

---

#### Task 2.3 — Wire participant list from config to broker + renderer

- [ ] In `scripts/start_interactive_tmux.py`, load `config.yaml` (use `yaml.safe_load`) to get the participant ids list. Add a new arg `--config` defaulting to `PROJECT_ROOT / "config.yaml"`.
- [ ] Build `participant_ids = [p["id"] for p in config["participants"]]`. Default to `["claude", "codex"]` if config missing.
- [ ] Pass `participants=participant_ids` to `TmuxRenderer(...)`.
- [ ] Build `participant_commands: dict[str, str]` mapping each id to the right CLI invocation. For `claude` → use existing `build_windows_agent_command(PROJECT_ROOT, "claude")`. For `codex` → `build_windows_agent_command(PROJECT_ROOT, "codex --dangerously-bypass-approvals-and-sandbox")`. For unknown ids, fall back to `build_windows_agent_command(PROJECT_ROOT, pid)` (just run a CLI matching the id).
- [ ] Pass `participant_commands=...` to `renderer.start_interactive(...)`.
- [ ] Update `room_broker.py:main` (the CLI entrypoint at the bottom): also load `config.yaml` and pass `targets=participant_ids` to `RoomBroker(...)`.
- [ ] Update `tests/test_start_interactive_tmux.py` — assert that `participants` and `participant_commands` are passed through correctly.

**Acceptance:** running `.\scripts\start-tmux-roundtable.ps1` with default config still works exactly as before (2 participants). Editing `config.yaml` to add a 3rd participant entry results in 3 right-side panes and a working `/debate` flow including the 3rd agent.

---

### Phase 3 — Verification

#### Task 3.1 — Full test suite + smoke test

- [ ] Run `python -m pytest tests/ -v` — expect 162+ passing (existing) plus new tests added in tasks above. Zero failures.
- [ ] Run a manual smoke test:
  1. Default 2-participant config: `.\scripts\start-tmux-roundtable.ps1` → verify `/debate 2 vibe coding` runs end-to-end with Claude→Codex order maintained both rounds
  2. Add a fake 3rd participant (e.g. `id: gemini`, `plugin: claude` so it actually invokes claude CLI for now) to `config.yaml` → restart → verify 3 panes appear and `/debate 1 test topic` cycles through all 3
- [ ] Confirm `End Reason` in the generated markdown report is no longer `None` after a `user_ended` exit

**Acceptance:** all automated tests pass. Manual smoke tests both succeed.

---

## Notes for the Implementer (Codex)

1. **Keep diffs minimal and focused.** Each task above is meant to land as one or two commits. Don't bundle unrelated changes (e.g. don't reformat untouched files, don't add new abstractions beyond what the task lists).

2. **Maintain backward compatibility.** Default behavior (2 participants, claude+codex) must remain identical after this plan. Reviewer will run the existing smoke flow and expect the same outcome.

3. **Keep test count growing, never shrinking.** If you delete a test, justify it. Prefer adding new ones for new behavior.

4. **Don't touch the things this plan does not touch.** Specifically: do not change `Orchestrator`, `ContextManager`, `EventLog`, `report.py`, MCP server code, or any prompt strings in `room_broker.py`. Those are separate plans.

5. **Verify each phase before moving on.** Phase 1 should produce a green test suite before Phase 2 starts. Phase 2 likewise before Phase 3.

6. **When in doubt, prefer small surface area.** E.g. for Task 2.2, exposing `broker.targets` as a read-only property is preferable to making the underlying list mutable.

---

## Review Checklist (for the reviewer)

When Codex reports the plan complete, the reviewer (Claude) will check:

- [ ] All 8 tasks above marked `- [x]` complete
- [ ] `git diff` confined to the files listed under "Target File Structure"
- [ ] `python -m pytest tests/` shows 162+ passing, 0 failing
- [ ] `grep -rn "_handle_degraded\|KNOWN_TARGETS\b" --include="*.py" scripts/ src/ main.py` returns only the expected residual mentions (parser default in room_broker.py)
- [ ] `grep -rn '"claude"\|"codex"' --include="*.py" src/renderers/tmux.py` returns NO hard-coded matches inside function bodies (defaults in arg lists are OK)
- [ ] Manual smoke test (2-participant default) produces identical UX to before
- [ ] Manual smoke test (3-participant) creates 3 panes and cycles all 3 in debate
- [ ] Generated report's `End Reason` is `user_ended` (not `None`) after stalemate→end exit
