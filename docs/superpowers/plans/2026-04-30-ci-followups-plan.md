# AI Roundtable — CI Follow-ups Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`).

**Goal:** Wrap up three remaining housekeeping items left over from the CI/LICENSE plan: upgrade GitHub Actions to current versions (Node 24 ready), add discoverability topics to the GitHub repo, and prepare codecov upload to be a one-step enable once the user adds the `CODECOV_TOKEN` secret.

**Out of scope:**

- Renaming the default branch from `master` to `main` (intentionally skipped per user decision).
- Adding the `CODECOV_TOKEN` secret itself — that requires a browser session at https://github.com/wwb1942/ai-roundtable/settings/secrets/actions and is left to the human.
- Any application source code changes. Tests must remain at 184 on Windows local runs and 178 + 6 skipped on Linux CI.

**Tech Stack:** GitHub Actions YAML edits, `gh` CLI for repo metadata. No Python source changes.

---

## Problem Analysis

After the previous CI plan landed, three follow-ups remain:

1. **Node.js 20 deprecation warning.** The current CI run prints:
   > Node.js 20 actions are deprecated. The following actions are running on Node.js 20 and may not work as expected: actions/checkout@v4, actions/setup-python@v5, actions/upload-artifact@v4. Actions will be forced to run with Node.js 24 by default starting June 2nd, 2026.

   The fix is to bump each action to a Node-24-compatible version. As of this plan's date, the latest are:
   - `actions/checkout@v5` (Node 24 native)
   - `actions/setup-python@v6` (Node 24 native)
   - `actions/upload-artifact@v5` (Node 24 native)

   These are minor-version bumps, no breaking API changes for the inputs we use (`python-version`, `cache`, `name`, `path`, `if-no-files-found`).

2. **GitHub topic tags missing.** The repo has a description but no topics, so it doesn't surface in topic-based search. Adding 5 topics (`mcp`, `multi-agent`, `claude-code`, `tmux`, `python`) is one `gh repo edit` call.

3. **Codecov upload is currently commented out.** `.github/workflows/ci.yml` has the step prepared but commented because the `CODECOV_TOKEN` secret is not yet set. Plan goal: make it so the user only needs to (a) sign up at codecov.io, (b) add the secret, and the next push automatically uploads. Codex CANNOT add the secret — that is browser-only — but can:
   - Uncomment the upload step
   - Make it conditional on the secret being present (`if: ${{ secrets.CODECOV_TOKEN != '' }}`) so missing-secret runs don't fail
   - Update README badge to point at codecov

---

## Target File Structure

Edits only:

```text
.github/workflows/ci.yml      # bump action versions, enable conditional codecov upload
README.md                     # add codecov badge
```

No new files. No source code changes.

External actions (NOT done by Codex, listed here for reviewer clarity):

- `gh repo edit wwb1942/ai-roundtable --add-topic mcp,multi-agent,claude-code,tmux,python`
- `gh repo view wwb1942/ai-roundtable` (verify topics applied)
- These are `gh CLI` calls. Codex MAY attempt them; if `gh` reports network/auth failure, STOP and let the human handle it.

---

## Implementation Plan

### Phase 1 — Upgrade actions to Node 24 versions

#### Task 1.1 — Bump `.github/workflows/ci.yml` action versions

- [ ] Edit `.github/workflows/ci.yml`. Apply EXACTLY these three line changes; do not touch anything else in the file:
  - `- uses: actions/checkout@v4` → `- uses: actions/checkout@v5`
  - `uses: actions/setup-python@v5` → `uses: actions/setup-python@v6`
  - `uses: actions/upload-artifact@v4` → `uses: actions/upload-artifact@v5`
- [ ] Verify the YAML still parses: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml').read())"` — must exit 0.
- [ ] Confirm via grep that no `@v4` or `@v5` references for `setup-python` remain in the workflow file:
  - `grep -E "actions/(checkout|setup-python|upload-artifact)@" .github/workflows/ci.yml` should show ONLY `@v5`, `@v6`, `@v5` respectively.

**Acceptance:** YAML valid. Three (and only three) lines changed in the workflow. Inputs (`python-version`, `cache`, `name`, `path`, `if-no-files-found`) untouched.

---

### Phase 2 — Enable conditional codecov upload

#### Task 2.1 — Replace commented codecov block with conditional version

- [ ] In `.github/workflows/ci.yml`, find the commented-out block:
  ```yaml
  # - name: Upload to Codecov (uncomment after setting CODECOV_TOKEN secret)
  #   uses: codecov/codecov-action@v4
  #   with:
  #     token: ${{ secrets.CODECOV_TOKEN }}
  #     files: coverage.xml
  ```
- [ ] Replace it with this active, conditional version (note `@v5` for codecov-action, the current major as of plan date; and the `if:` condition that gates execution on the secret being non-empty):
  ```yaml
        - name: Upload to Codecov
          if: ${{ secrets.CODECOV_TOKEN != '' }}
          uses: codecov/codecov-action@v5
          with:
            token: ${{ secrets.CODECOV_TOKEN }}
            files: coverage.xml
            fail_ci_if_error: false
  ```
- [ ] Verify YAML still parses (same `python -c yaml.safe_load` check).

**Acceptance:** the codecov step is no longer commented out. CI runs without the secret skip this step gracefully (the `if:` clause means the step is "skipped", not "failed"). When the user later adds the secret, the next push uploads automatically without further code changes.

---

### Phase 3 — README codecov badge (optional-state-aware)

#### Task 3.1 — Add codecov badge alongside the CI badge

- [ ] In `README.md`, find the existing two-badge line at the top:
  ```markdown
  [![CI](https://github.com/wwb1942/ai-roundtable/actions/workflows/ci.yml/badge.svg)](https://github.com/wwb1942/ai-roundtable/actions/workflows/ci.yml)
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
  ```
- [ ] Insert a third badge BETWEEN the CI badge and the License badge:
  ```markdown
  [![codecov](https://codecov.io/gh/wwb1942/ai-roundtable/graph/badge.svg)](https://codecov.io/gh/wwb1942/ai-roundtable)
  ```
- [ ] Final order in the file should be: CI · codecov · License (one badge per line).
- [ ] Note for the human: until codecov.io has been signed up and the first upload landed, the codecov badge will display "unknown". This is expected and not a CI failure.

**Acceptance:** three badges present at the top of README, in the order CI, codecov, License. No other README changes.

---

### Phase 4 — Apply repo topics (best-effort, non-blocking)

#### Task 4.1 — Add discoverability topics via `gh CLI`

- [ ] Run `gh auth status` to confirm gh is authenticated. If not authenticated, STOP and report — do not attempt OAuth flow.
- [ ] Run:
  ```bash
  gh repo edit wwb1942/ai-roundtable --add-topic mcp,multi-agent,claude-code,tmux,python,roundtable
  ```
- [ ] Verify with:
  ```bash
  gh repo view wwb1942/ai-roundtable --json repositoryTopics
  ```
  Confirm the response lists at least the 6 topics added.
- [ ] If `gh` returns ANY network error (TLS reset, GraphQL timeout, 4xx/5xx, auth failure, etc.), DO NOT retry more than once. Report the exact error and STOP. The human will run the same command from their session.

**Acceptance:** topics show in `gh repo view --json repositoryTopics` output. Skipped if network blocked, with clear report.

---

### Phase 5 — Local verification & stop for review

#### Task 5.1 — Final verification

- [ ] `git status --short` shows exactly:
  - `.github/workflows/ci.yml` (modified)
  - `README.md` (modified)
  - (No other files; if there are, investigate.)

- [ ] Run `python -m pytest tests/ -q` — must show 184 passed (Windows) with same count and status as before this plan started.

- [ ] Run YAML validation again:
  ```bash
  python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml').read())"
  ```

- [ ] Print the final state of `.github/workflows/ci.yml` (cat the whole file) so the reviewer can see:
  - All three action versions are bumped
  - Codecov step is uncommented and conditional
  - No other accidental edits

#### Task 5.2 — Stop for reviewer

- [ ] Report:
  - The 3 bumped action versions, line-by-line confirmation
  - The codecov step's new YAML (paste it back)
  - The result of `gh repo view` topic verification (or error if network blocked)
  - `git status --short` output
  - `git diff --stat` for the two changed files
- [ ] DO NOT commit. DO NOT push. DO NOT touch any other gh command (no `gh secret`, no `gh api`, no PRs).

**Acceptance:** reviewer confirms before commit/push step.

---

## Notes for the Implementer (Codex)

1. **No source code changes.** Only `.github/workflows/ci.yml` and `README.md`. If any other file shows up in `git status`, stop and report.

2. **Three action version bumps are exact line-for-line.** Do not "modernize" anything else — no caching tweaks, no matrix builds, no concurrency rules. The previous plan deliberately picked single-platform single-version simplicity.

3. **Do not push.** Do not commit. Do not add the `CODECOV_TOKEN` secret. Do not run `gh secret set`. Do not run `gh repo edit --default-branch`. Reviewer handles those.

4. **Phase 4 is best-effort.** If `gh repo edit --add-topic` fails for any reason (network, permission, rate limit), report the failure and continue to Phase 5. Do not retry more than once. Do not attempt to "fix" auth or change `gh` config.

5. **If a tool/action version you reference no longer exists** (e.g. `actions/checkout@v5` is yanked), STOP and ask. Do not pick a different version on your own.

---

## Review Checklist (for the reviewer)

- [ ] `.github/workflows/ci.yml` shows three version bumps (checkout v5, setup-python v6, upload-artifact v5) and codecov-action v5 active with `if:` condition
- [ ] `README.md` has 3 badges in order: CI, codecov, License
- [ ] No source code files in `git diff`
- [ ] `pytest` shows 184 passed locally
- [ ] YAML still valid
- [ ] Topics applied (or clearly reported as network-blocked)
- [ ] No commit, no push from Codex
