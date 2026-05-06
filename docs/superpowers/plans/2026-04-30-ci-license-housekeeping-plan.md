# AI Roundtable — CI, LICENSE, Housekeeping Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`).

**Goal:** Add a GitHub Actions CI workflow (pytest + ruff + mypy + coverage report), a LICENSE file (MIT), and the small README polish that comes with becoming a published open-source project. After this plan, every push and PR runs the full verification suite automatically.

**Scope boundary:** This plan does NOT change application code. All edits are configuration, metadata, and tooling. The 184 existing tests must keep passing without modification (other than the testing config file we add).

**Tech Stack:** GitHub Actions, ruff (any recent version), mypy (any recent version), pytest with coverage. No new runtime dependencies for the application.

---

## Problem Analysis

The repo just landed on GitHub at `wwb1942/ai-roundtable`. It has:

- 15 commits with conventional commit messages
- 184 passing tests (`python -m pytest tests/`)
- A working README and architecture doc
- **No LICENSE** — others legally cannot use, fork, or redistribute the code
- **No CI** — regressions can land silently
- **No lint/type checking** — code style and type errors only caught at review time

Three small additions close all three gaps. They are independent and can land in three commits.

**Three constraints we have to honor:**

1. **mypy from cold start will fail.** The codebase uses `Any` in several `Orchestrator` method signatures (intentional — the plugin Protocol is in `src/plugin_base.py` but `orchestrator.py` types its plugins list as `list[Any]`). A strict mypy run will surface dozens of warnings. We will configure mypy in **lenient mode for this first pass**: target only `src/`, ignore missing imports, do not enable strict optional. Future tightening is out of scope.

2. **Coverage upload needs a secret.** Codecov requires a `CODECOV_TOKEN` secret added in GitHub repo settings. The plan generates the coverage report and stores it as a workflow artifact, but does NOT upload to codecov. The user (Mick) configures the secret separately if/when desired; the workflow file leaves a commented-out upload step ready to enable.

3. **Tests must stay 184 green.** No test modifications. If a tool flags an existing test, fix the tool config, not the test.

---

## Target File Structure

New files:

```text
LICENSE                       # MIT, with year 2026 and copyright holder = Mick Wang
.github/workflows/ci.yml      # ubuntu-latest + Python 3.12, runs all 4 steps
pyproject.toml                # tool configs: ruff, mypy, pytest+coverage
```

Edit:

```text
README.md                     # add CI badge at top, LICENSE section near bottom
```

No source-code edits. No test edits.

---

## Implementation Plan

### Phase 1 — LICENSE

#### Task 1.1 — Add MIT LICENSE

- [ ] Create `LICENSE` at repo root with the canonical MIT text. Year: `2026`. Copyright holder: `Mick Wang`. Use the exact OSI-approved wording (https://opensource.org/license/mit/).
- [ ] Verify SPDX identifier present at the top of file: `MIT License` is fine; the second line stays as the standard "Copyright (c) 2026 Mick Wang" line.

**Acceptance:** `LICENSE` exists, contains the MIT text with correct year and holder. `git diff --stat` shows exactly one new file.

---

### Phase 2 — pyproject.toml with tool configs

#### Task 2.1 — Create `pyproject.toml` with ruff / mypy / pytest+coverage configs

- [ ] Create `pyproject.toml` at repo root with these sections:

  ```toml
  [tool.ruff]
  line-length = 120
  target-version = "py312"

  [tool.ruff.lint]
  # Conservative starter set: pycodestyle errors, pyflakes, import sort
  select = ["E", "F", "I", "W"]
  # Ignore rules that are commonly noisy in early-stage Python projects
  ignore = ["E501"]  # line-too-long handled by formatter, not lint

  [tool.ruff.lint.per-file-ignores]
  "tests/**" = ["F401"]  # test fixtures sometimes import for side-effects

  [tool.mypy]
  python_version = "3.12"
  files = ["src"]
  ignore_missing_imports = true
  # Lenient first-pass: surface obvious errors but do not require strict typing
  check_untyped_defs = false
  disallow_untyped_defs = false
  warn_return_any = false
  warn_unused_ignores = true
  # Modules that intentionally use Any-typed Protocol-driven plugins:
  # the orchestrator's Any pattern is documented in the architecture spec
  [[tool.mypy.overrides]]
  module = "src.orchestrator"
  ignore_errors = true

  [tool.pytest.ini_options]
  testpaths = ["tests"]
  addopts = "-q --strict-markers"

  [tool.coverage.run]
  source = ["src", "scripts"]
  omit = [
      "tests/*",
      "*/.venv/*",
  ]
  branch = true

  [tool.coverage.report]
  show_missing = true
  skip_covered = false
  precision = 1
  ```

- [ ] Run each tool locally to confirm config is valid before committing:
  - `python -m pip install ruff mypy pytest coverage` (in `.venv`)
  - `ruff check src/ scripts/ tests/` — note count of errors but DO NOT fix any source code; if ruff finds pre-existing issues, add them to `tool.ruff.lint.ignore` only if there are ≤ 5 of a specific rule, otherwise STOP and ask
  - `mypy` — same rule: lenient config above should make it green; if not, expand the `[[tool.mypy.overrides]]` section, do not change source
  - `pytest tests/` — must show 184 passed, exit code 0
  - `coverage run -m pytest tests/ && coverage report` — must complete with no errors; the percentage number is informational

**Acceptance:** all four tool invocations exit zero locally. `pyproject.toml` is the only new file in this commit.

---

### Phase 3 — GitHub Actions CI

#### Task 3.1 — Create `.github/workflows/ci.yml`

- [ ] Create `.github/workflows/ci.yml` with this structure:

  ```yaml
  name: CI

  on:
    push:
      branches: [master, main]
    pull_request:
      branches: [master, main]

  jobs:
    test:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4

        - name: Set up Python 3.12
          uses: actions/setup-python@v5
          with:
            python-version: "3.12"
            cache: pip

        - name: Install dependencies
          run: |
            python -m pip install --upgrade pip
            pip install -r requirements.txt
            pip install ruff mypy pytest coverage

        - name: Lint (ruff)
          run: ruff check src/ scripts/ tests/

        - name: Type check (mypy)
          run: mypy

        - name: Run tests with coverage
          run: |
            coverage run -m pytest tests/
            coverage report
            coverage xml -o coverage.xml

        - name: Upload coverage artifact
          uses: actions/upload-artifact@v4
          with:
            name: coverage-report
            path: coverage.xml
            if-no-files-found: error

        # - name: Upload to Codecov (uncomment after setting CODECOV_TOKEN secret)
        #   uses: codecov/codecov-action@v4
        #   with:
        #     token: ${{ secrets.CODECOV_TOKEN }}
        #     files: coverage.xml
  ```

- [ ] Verify the YAML parses by running `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml').read())"` — must exit 0.
- [ ] DO NOT push the workflow yet. Reviewer (me, Claude) will validate the YAML structure before committing.

**Acceptance:** YAML is well-formed, contains all 5 steps in order (checkout, setup-python, install, lint, type, test+coverage, upload artifact), and the Codecov upload step is commented out with a clear hint.

---

### Phase 4 — README polish

#### Task 4.1 — Add CI badge and LICENSE section to README

- [ ] At the very top of `README.md` (immediately after the `# AI Roundtable` heading, before the description paragraph), add:

  ```markdown
  [![CI](https://github.com/wwb1942/ai-roundtable/actions/workflows/ci.yml/badge.svg)](https://github.com/wwb1942/ai-roundtable/actions/workflows/ci.yml)
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
  ```

- [ ] At the BOTTOM of `README.md` (after the "Known Limitations" section), add:

  ```markdown
  ## License

  MIT — see [LICENSE](LICENSE) for the full text.
  ```

**Acceptance:** README has the two badges right under the H1, and a License section at the bottom. Existing sections untouched.

---

### Phase 5 — Local verification & commit prep

#### Task 5.1 — Final local verification

- [ ] Run all four tool commands again, in order. All must exit 0:
  - `ruff check src/ scripts/ tests/`
  - `mypy`
  - `pytest tests/` — expect 184 passed
  - `coverage run -m pytest tests/ && coverage report`

- [ ] `git status --short` shows exactly these new/modified files:
  - `LICENSE` (new)
  - `pyproject.toml` (new)
  - `.github/workflows/ci.yml` (new)
  - `README.md` (modified)

  Anything else means a tool wrote a stray file. Investigate before committing.

- [ ] DO NOT push. DO NOT run gh CLI. DO NOT create commits yet — let me (the reviewer) check first.

#### Task 5.2 — Pause for reviewer

- [ ] Stop here and report to the human:
  - List of new files with sizes
  - The output of `ruff check ...` (should be empty / clean)
  - The output of `mypy` (should be: "Success: no issues found in N source files" or similar)
  - Coverage percentage from `coverage report`
  - Confirm: no application source code was modified

**Acceptance:** reviewer confirms before commit step.

---

## Notes for the Implementer (Codex)

1. **Do not modify application source code.** All four tasks are config/docs only. If ruff or mypy flags a code issue, the fix is to expand the tool's ignore list in `pyproject.toml`, not to edit `src/` or `scripts/`. If you cannot make a tool green via config alone, STOP and ask.

2. **Do not modify tests.** If pytest count drops below 184, something is wrong. Stop and report.

3. **Mypy is supposed to be lenient.** This is the first pass. Future plans can tighten it. If a single override doesn't silence all errors, add more overrides — don't refactor source.

4. **Do not push.** Do not commit. Do not touch gh. The reviewer will run those steps after validating each artifact.

5. **Do not enable codecov upload.** It needs a secret that the reviewer hasn't set yet. Leave the step commented out exactly as specified.

6. **If a tool version you install behaves differently than this plan describes** (ruff renamed a flag, mypy changed defaults), STOP and report. Do not improvise config.

---

## Review Checklist (for the reviewer)

- [ ] Three new files only: `LICENSE`, `pyproject.toml`, `.github/workflows/ci.yml`
- [ ] `README.md` modified only to add 2 badges at top and License section at bottom
- [ ] `git diff` shows zero application source code changes (`src/**`, `scripts/**`, `main.py`, `tests/**` untouched except possibly `pyproject.toml` adding pytest config)
- [ ] `ruff check src/ scripts/ tests/` runs clean
- [ ] `mypy` runs clean
- [ ] `pytest tests/` shows 184 passed, 0 failed
- [ ] `coverage report` produces a percentage (no minimum required this round)
- [ ] `.github/workflows/ci.yml` parses as valid YAML
- [ ] LICENSE has correct year (2026) and holder (Mick Wang)
- [ ] Codecov upload step is COMMENTED OUT (secret not yet set)
