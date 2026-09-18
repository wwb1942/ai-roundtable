# Roundtable Maintainer

Roundtable Maintainer is the task-focused workflow built on the batch
roundtable core. The roundtable still makes the judgment calls: participants
independently diagnose the issue, cross-examine one another, and later review
the resulting patch. Deterministic operations such as creating a worktree,
collecting a diff, and running checks are performed directly by the workflow.

## Lifecycle

```text
source repository
  -> isolated Git worktree
  -> independent diagnosis
  -> cross-examination
  -> one implementation participant
  -> deterministic checks
  -> independent patch review
  -> cross-examination
  -> awaiting_approval | needs_attention | failed
```

The diagnosis and review gates use the participants from `config.yaml`. Round
one gives every participant the same frozen context, so proposals are
independent. Later rounds include the preceding responses and explicitly ask
participants to challenge unsupported assumptions and reconcile differences.

Only one participant writes the implementation. Selection order is:

1. The participant named by `--executor`.
2. A participant whose ID is `codex`.
3. The first participant advertising artifact support.
4. The first configured participant.

## Usage

Describe the issue inline:

```powershell
python main.py maintain `
  --repo E:\projects\example `
  --issue "The retry counter is incremented twice" `
  --check "python -m pytest tests/test_retry.py" `
  --check "python -m ruff check src tests"
```

Or provide a UTF-8 issue file:

```powershell
python main.py maintain `
  --repo E:\projects\example `
  --issue-file .\issue.md `
  --base-ref HEAD `
  --rounds 2
```

Important options:

- `--config`: participant configuration, default `config.yaml`.
- `--base-ref`: committed revision used to create the worktree, default `HEAD`.
- `--executor`: participant allowed to implement the change.
- `--rounds`: number of diagnosis and review rounds, default `2`.
- `--check`: verification command; quote it and repeat it as needed.
- `--check-timeout`: timeout for each verification command, default 300 seconds.
- `--output-dir`: explicit run directory.
- `--allow-dirty`: permit a dirty source repository. Its uncommitted changes
  are not present in the worktree.

## Run Artifacts

Without `--output-dir`, each run is stored beside the source repository:

```text
<repo-parent>/.roundtable-runs/<repo-name>/<run-id>/
  workspace/       isolated Git worktree, retained for inspection
  report.md        human-readable diagnosis, checks, and review
  report.json      structured result and complete bounded check output
  state.json       latest workflow state
  changes.patch    tracked and untracked changes in Git patch form
  events.jsonl     workflow event stream
```

The worktree branch is named `roundtable/<issue-slug>-<short-id>`. The workflow
does not commit the patch. If an implementation participant commits despite the
instruction, the commit is preserved for inspection and the result becomes
`needs_attention`.

## Result States

- `awaiting_approval`: the executor returned normally, a patch exists, at least
  one check passed, every check passed, reviewers did not mutate the worktree,
  the review converged, and no participant error occurred.
- `needs_attention`: the run produced inspectable output but failed one or more
  gates, such as a failed check, no patch, an unexpected mutation or commit, a
  participant error, or review disagreement.
- `failed`: repository preparation or another required deterministic operation
  could not complete safely.

The state is a review gate, not an automatic merge decision. Inspect
`report.md`, `changes.patch`, and the preserved worktree before committing.
The CLI exits with status `0` only for `awaiting_approval`; `needs_attention`
and `failed` exit with status `1` so automation cannot mistake them for an
approved patch.

## Safety Boundary

Issue descriptions, repository content, diffs, test output, and participant
responses are treated as untrusted evidence in the workflow prompts. Git and
check commands use argument arrays and never enable a command shell. The
workflow does not add approval-bypass flags to agent CLIs. Repository hooks
are disabled while the isolated worktree is created.

The working directory is not a security sandbox. Read-only diagnosis and
review are enforced by participant instructions plus before/after Git
snapshots of both the isolated worktree and source repository. Those snapshots
cover tracked, untracked, and ignored files, symbolic HEAD, shared Git refs,
and repository-local/worktree Git config. An agent that mutates this state causes
`needs_attention`, and an investigation mutation stops the workflow before
implementation. Use the CLI's own sandbox controls and run untrusted
repositories in an isolated environment; snapshots do not prevent changes
outside the repositories.

## Cleanup

After reviewing or moving the desired changes, remove the retained worktree
from the source repository and then delete its branch:

```powershell
git -C E:\projects\example worktree remove <run-dir>\workspace
git -C E:\projects\example branch -D <roundtable-branch>
```

These commands are intentionally manual because removing the worktree can
discard uncommitted Maintainer output.
