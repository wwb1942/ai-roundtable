from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.maintainer import MaintainerState, MaintainerWorkflow
from src.models import ParticipantTurnResult, RoundContext


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _make_repo(path: Path) -> tuple[Path, str]:
    path.mkdir()
    _git(path, "init", "--quiet")
    _git(path, "config", "user.name", "Roundtable Tests")
    _git(path, "config", "user.email", "roundtable@example.invalid")
    (path / "app.py").write_text("VALUE = 'broken'\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    _git(path, "add", "app.py", "pyproject.toml")
    _git(path, "commit", "--quiet", "-m", "initial")
    return path, _git(path, "rev-parse", "HEAD")


class FakeMaintainerPlugin:
    capabilities = {
        "session_mode": "context-managed",
        "interruptible": False,
        "structured_status": True,
        "supports_resume": False,
        "supports_artifacts": True,
    }

    def __init__(
        self,
        plugin_id: str,
        *,
        check_status: str = "converging",
        fail_investigation: bool = False,
        fail_review: bool = False,
        mutate_investigation: bool = False,
        mutate_review: bool = False,
        mutate_refs_review: bool = False,
        mutate_config_review: bool = False,
        mutate_worktree_config_review: bool = False,
        mutate_ignored_investigation: bool = False,
        commit_implementation: bool = False,
        detach_implementation: bool = False,
        source_to_mutate: Path | None = None,
        mutate_source_during_investigation: bool = False,
    ) -> None:
        self.id = plugin_id
        self.display_name = plugin_id.title()
        self.check_status = check_status
        self.fail_investigation = fail_investigation
        self.fail_review = fail_review
        self.mutate_investigation = mutate_investigation
        self.mutate_review = mutate_review
        self.mutate_refs_review = mutate_refs_review
        self.mutate_config_review = mutate_config_review
        self.mutate_worktree_config_review = mutate_worktree_config_review
        self.mutate_ignored_investigation = mutate_ignored_investigation
        self.commit_implementation = commit_implementation
        self.detach_implementation = detach_implementation
        self.source_to_mutate = source_to_mutate
        self.mutate_source_during_investigation = mutate_source_during_investigation
        self.executor_calls = 0
        self.contexts: list[RoundContext] = []

    def validate(self) -> dict:
        return {"ok": True, "errors": [], "warnings": []}

    def start_session(self, topic: str, system_contract: str) -> None:
        return None

    def send_turn(self, session_id: str | None, context: RoundContext) -> ParticipantTurnResult:
        self.contexts.append(context)
        root = Path(context["working_directory"])
        contract = context["system_contract"]
        if (
            ("diagnostic participant" in contract and self.fail_investigation)
            or ("review participant" in contract and self.fail_review)
        ):
            return ParticipantTurnResult(
                content="", raw_output="", status="unknown", status_summary=None,
                artifacts=[],
                error={
                    "code": "timeout",
                    "message": (
                        "simulated diagnostic timeout"
                        if "diagnostic participant" in contract
                        else "simulated review timeout"
                    ),
                    "detail": None,
                },
                duration_ms=1, session_id=None, token_usage=None, cost_usd=None,
            )
        if "diagnostic participant" in contract and self.mutate_investigation:
            (root / "investigation-mutation.txt").write_text("unexpected\n", encoding="utf-8")
        if "diagnostic participant" in contract and self.mutate_ignored_investigation:
            (root / "ignored-mutation.txt").write_text("unexpected\n", encoding="utf-8")
        if (
            "diagnostic participant" in contract
            and self.mutate_source_during_investigation
            and self.source_to_mutate is not None
        ):
            (self.source_to_mutate / "app.py").write_text("VALUE = 'source-mutated'\n", encoding="utf-8")
        if "implementation participant" in contract:
            self.executor_calls += 1
            (root / "app.py").write_text("VALUE = 'fixed'\n", encoding="utf-8")
            (root / "test_regression.py").write_text(
                "from app import VALUE\n\ndef test_value():\n    assert VALUE == 'fixed'\n",
                encoding="utf-8",
            )
            if self.commit_implementation:
                _git(root, "add", "app.py", "test_regression.py")
                _git(root, "commit", "--quiet", "-m", "agent commit")
            if self.detach_implementation:
                _git(root, "checkout", "--quiet", "--detach", "HEAD")
            if self.source_to_mutate is not None:
                (self.source_to_mutate / "app.py").write_text("VALUE = 'source-mutated'\n", encoding="utf-8")
        if "review participant" in contract and self.mutate_review:
            (root / "review-mutation.txt").write_text("unexpected\n", encoding="utf-8")
        if "review participant" in contract and self.mutate_refs_review:
            _git(root, "tag", "review-created-tag")
        if "review participant" in contract and self.mutate_config_review:
            _git(root, "config", "--local", "roundtable.mutated", "yes")
        if "review participant" in contract and self.mutate_worktree_config_review:
            _git(root, "config", "--worktree", "roundtable.worktree-mutated", "yes")
        return ParticipantTurnResult(
            content=f"{self.id} evidence for round {context['round_number']}",
            raw_output="",
            status=self.check_status,
            status_summary="evidence reviewed",
            artifacts=[],
            error=None,
            duration_ms=1,
            session_id=None,
            token_usage=None,
            cost_usd=None,
        )

    def close_session(self, session_id: str | None) -> None:
        return None


def _passing_check() -> list[str]:
    return [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "assert \"VALUE = 'fixed'\" in Path('app.py').read_text(); "
            "assert Path('test_regression.py').is_file()"
        ),
    ]


def test_success_keeps_source_clean_and_writes_complete_run_artifacts(tmp_path: Path):
    repo, base_commit = _make_repo(tmp_path / "source")
    plugins = [FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")]
    run_dir = tmp_path / "run"

    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix the broken value and add a regression test",
        plugins=plugins,
        rounds=2,
        checks=[_passing_check()],
        output_dir=run_dir,
    ).run()

    assert result.state == MaintainerState.AWAITING_APPROVAL
    assert result.worktree is not None
    assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 'broken'\n"
    assert _git(repo, "status", "--porcelain") == ""
    assert (result.worktree / "app.py").read_text(encoding="utf-8") == "VALUE = 'fixed'\n"
    assert _git(result.worktree, "rev-parse", "HEAD") == base_commit
    assert result.branch and result.branch.startswith("roundtable/fix-the-broken-value")
    assert len(result.diagnostics) == 4
    assert len(result.review) == 4
    assert all(check.passed for check in result.checks)

    for artifact in ("report.md", "report.json", "state.json", "changes.patch", "events.jsonl"):
        assert (run_dir / artifact).is_file()
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["state"] == "awaiting_approval"
    assert report["issue"] == "Fix the broken value and add a regression test"
    assert report["implementation"]["content"] == "codex evidence for round 1"
    assert report["diagnostic_end_reason"] == "converged"
    assert report["review_end_reason"] == "converged"
    assert report["started_at"]
    assert report["finished_at"]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["base_commit"] == base_commit
    markdown = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## Issue" in markdown
    assert "## Implementation Result" in markdown
    patch = (run_dir / "changes.patch").read_text(encoding="utf-8")
    assert "VALUE = 'fixed'" in patch
    assert "test_regression.py" in patch
    assert "assert VALUE == 'fixed'" in patch


def test_dirty_source_repository_is_rejected_by_default(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    (repo / "app.py").write_text("dirty\n", encoding="utf-8")

    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.FAILED
    assert result.worktree is None
    assert any("dirty" in reason for reason in result.reasons)


def test_allow_dirty_uses_committed_base_without_touching_source_changes(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    (repo / "app.py").write_text("VALUE = 'local-only'\n", encoding="utf-8")

    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
        allow_dirty=True,
    ).run()

    assert result.state == MaintainerState.AWAITING_APPROVAL
    assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 'local-only'\n"
    assert _git(repo, "status", "--porcelain") == "M app.py"


def test_output_directory_inside_source_is_rejected_before_writing(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    output_dir = repo / "maintainer-output"
    workflow = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=[_passing_check()],
        output_dir=output_dir,
    )

    with pytest.raises(ValueError, match="outside the source repository"):
        workflow.run()

    assert not output_dir.exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_nonempty_run_directory_is_not_overwritten(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    output_dir = tmp_path / "existing-run"
    output_dir.mkdir()
    marker = output_dir / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")

    workflow = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=[_passing_check()],
        output_dir=output_dir,
    )

    with pytest.raises(FileExistsError, match="not empty"):
        workflow.run()

    assert marker.read_text(encoding="utf-8") == "keep\n"
    assert list(output_dir.iterdir()) == [marker]


def test_default_run_directory_uses_git_root_when_repo_is_a_subdirectory(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    nested = repo / "packages" / "sample"
    nested.mkdir(parents=True)
    (nested / "marker.txt").write_text("tracked\n", encoding="utf-8")
    _git(repo, "add", "packages/sample/marker.txt")
    _git(repo, "commit", "--quiet", "-m", "add nested project")

    result = MaintainerWorkflow(
        repo=nested,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=[_passing_check()],
    ).run()

    assert result.state == MaintainerState.AWAITING_APPROVAL
    assert result.run_dir.parent.parent == tmp_path / ".roundtable-runs"
    assert result.run_dir.parent.name == "source"
    assert _git(repo, "status", "--porcelain") == ""


def test_worktree_creation_does_not_run_repository_hooks(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    hook_output = repo / "hook-output.txt"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(
        f"#!/bin/sh\nprintf 'hook ran\\n' > '{hook_output.as_posix()}'\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.AWAITING_APPROVAL
    assert not hook_output.exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_diagnostic_degraded_reason_is_preserved(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[
            FakeMaintainerPlugin("claude", fail_investigation=True),
            FakeMaintainerPlugin("codex", check_status="diverging"),
        ],
        rounds=1,
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert result.diagnostic_end_reason == "degraded"


def test_diagnostic_stalemate_reason_is_preserved(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[
            FakeMaintainerPlugin("claude", check_status="stalemate"),
            FakeMaintainerPlugin("codex", check_status="stalemate"),
        ],
        rounds=1,
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert result.diagnostic_end_reason == "stalemate"
    assert result.review_end_reason == "stalemate"


def test_review_degraded_reason_is_preserved(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[
            FakeMaintainerPlugin("claude", fail_review=True),
            FakeMaintainerPlugin("codex"),
        ],
        rounds=1,
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert result.diagnostic_end_reason == "converged"
    assert result.review_end_reason == "degraded"


def test_failed_check_requires_attention(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=[[sys.executable, "-c", "raise SystemExit(7)"]],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert result.checks[0].exit_code == 7
    assert any("check failed" in reason for reason in result.reasons)


def test_explicit_executor_id_is_case_insensitive(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    executor = FakeMaintainerPlugin("codex")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), executor],
        executor_id="CODEX",
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.AWAITING_APPROVAL
    assert executor.executor_calls == 1


def test_investigator_mutation_stops_before_executor(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    investigator = FakeMaintainerPlugin("claude", mutate_investigation=True)
    executor = FakeMaintainerPlugin("codex")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[investigator, executor],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert executor.executor_calls == 0
    assert any("investigation participant" in reason for reason in result.reasons)


def test_source_mutation_during_investigation_stops_before_executor(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    investigator = FakeMaintainerPlugin(
        "claude",
        source_to_mutate=repo,
        mutate_source_during_investigation=True,
    )
    executor = FakeMaintainerPlugin("codex")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[investigator, executor],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert executor.executor_calls == 0
    assert any("source repository participant modified files" in reason for reason in result.reasons)


def test_investigator_ignored_file_mutation_stops_before_executor(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    (repo / ".gitignore").write_text("ignored-mutation.txt\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "--quiet", "-m", "ignore generated file")
    investigator = FakeMaintainerPlugin("claude", mutate_ignored_investigation=True)
    executor = FakeMaintainerPlugin("codex")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[investigator, executor],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert executor.executor_calls == 0
    assert any("investigation participant changed ignored files" in reason for reason in result.reasons)


def test_reviewer_mutation_requires_attention(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude", mutate_review=True), FakeMaintainerPlugin("codex")],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert any("review participant" in reason for reason in result.reasons)


def test_reviewer_git_ref_mutation_requires_attention(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude", mutate_refs_review=True), FakeMaintainerPlugin("codex")],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert any("review participant changed Git refs" in reason for reason in result.reasons)
    assert _git(repo, "tag", "--list", "review-created-tag") == "review-created-tag"


def test_reviewer_local_git_config_mutation_requires_attention(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude", mutate_config_review=True), FakeMaintainerPlugin("codex")],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert any("review participant changed repository-local Git config" in reason for reason in result.reasons)
    assert _git(repo, "config", "--local", "--get", "roundtable.mutated") == "yes"


def test_reviewer_worktree_git_config_mutation_requires_attention(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    _git(repo, "config", "extensions.worktreeConfig", "true")
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[
            FakeMaintainerPlugin("claude", mutate_worktree_config_review=True),
            FakeMaintainerPlugin("codex"),
        ],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert any("review participant changed worktree Git config" in reason for reason in result.reasons)


def test_executor_commit_requires_attention(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    executor = FakeMaintainerPlugin("codex", commit_implementation=True)
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), executor],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert any("changed HEAD" in reason for reason in result.reasons)
    review_topics = [
        context["topic"]
        for context in executor.contexts
        if "review participant" in context["system_contract"]
    ]
    assert review_topics
    assert "VALUE = 'fixed'" in review_topics[0]


def test_executor_detaching_head_requires_attention(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    executor = FakeMaintainerPlugin("codex", detach_implementation=True)
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), executor],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert any("detached HEAD" in reason for reason in result.reasons)


def test_flat_check_argv_preserves_arguments_containing_spaces(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    command = [sys.executable, "-c", "from pathlib import Path; Path('check ran.txt').write_text('ok')"]
    workflow = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=command,
        output_dir=tmp_path / "run",
    )

    assert workflow.checks == [command]


def test_single_check_string_is_split_as_a_command(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    workflow = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=["python -m pytest"],
        output_dir=tmp_path / "run",
    )

    assert workflow.checks == [["python", "-m", "pytest"]]


def test_check_command_commit_requires_attention(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    commit_check = [
        sys.executable,
        "-c",
        (
            "import subprocess; "
            "subprocess.run(['git', 'add', 'app.py', 'test_regression.py'], check=True); "
            "subprocess.run(['git', 'commit', '-m', 'check commit'], check=True)"
        ),
    ]
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        checks=[commit_check],
        output_dir=tmp_path / "run",
    ).run()

    assert result.checks[0].passed
    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert any("HEAD changed" in reason for reason in result.reasons)


def test_source_repository_mutation_requires_attention(tmp_path: Path):
    repo, _ = _make_repo(tmp_path / "source")
    executor = FakeMaintainerPlugin("codex", source_to_mutate=repo)
    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix it",
        plugins=[FakeMaintainerPlugin("claude"), executor],
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.state == MaintainerState.NEEDS_ATTENTION
    assert any("source repository participant modified files" in reason for reason in result.reasons)


def test_workspace_records_the_resolved_base_commit(tmp_path: Path):
    repo, first_commit = _make_repo(tmp_path / "source")
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "--quiet", "-m", "later")

    result = MaintainerWorkflow(
        repo=repo,
        issue="Fix the old revision",
        plugins=[FakeMaintainerPlugin("claude"), FakeMaintainerPlugin("codex")],
        base_ref=first_commit,
        checks=[_passing_check()],
        output_dir=tmp_path / "run",
    ).run()

    assert result.workspace is not None
    assert result.workspace.initial_head == first_commit
    assert result.worktree is not None
    assert not (result.worktree / "later.txt").exists()
