from pathlib import Path

import pytest
from test_core import contents, git
from typer.testing import CliRunner

from gitex.cli import app
from gitex.commands.integrate import integrate_branch
from gitex.models import Action, Outcome


@pytest.fixture
def workflow(repo: Path) -> tuple[Path, Path]:
    remote = repo.parent / (repo.name + "-remote.git")
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "branch", "develop")
    git(repo, "push", "-u", "origin", "main", "develop")
    git(repo, "checkout", "-b", "feature/topic", "develop")
    return repo, remote


def commit_file(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-m", text)
    return git(repo, "rev-parse", "HEAD")


def advance_source(repo: Path, source: str = "develop", file: str = "source") -> str:
    branch = git(repo, "branch", "--show-current")
    git(repo, "checkout", source)
    oid = commit_file(repo, file, "upstream")
    git(repo, "push")
    git(repo, "checkout", branch)
    return oid


def test_feature_rebase_and_repeat(workflow) -> None:
    repo, remote = workflow
    topic_oid = commit_file(repo, "topic", "topic")
    git(repo, "push", "-u", "origin", "feature/topic")
    source_oid = advance_source(repo)
    server = contents(remote)
    result = integrate_branch(repo)
    assert result.outcome == Outcome.UPDATED
    assert result.local_applied and not result.published
    assert "publication blocked" in result.reason
    assert git(repo, "rev-parse", "HEAD^") == source_oid
    assert git(repo, "rev-parse", "HEAD") != topic_oid
    assert (repo / "topic").read_text() == "topic"
    assert (repo / "source").read_text() == "upstream"
    assert git(repo, "status", "--porcelain") == ""
    assert contents(remote) == server
    assert integrate_branch(repo).outcome == Outcome.UNCHANGED


@pytest.mark.parametrize(
    "branch,source", [("release/v1", "main"), ("hotfix/v1", "main"), ("develop", "main")]
)
def test_merge_strategies(workflow, branch: str, source: str) -> None:
    repo, remote = workflow
    git(repo, "checkout", branch) if branch == "develop" else git(repo, "checkout", "-b", branch)
    original = commit_file(repo, "topic", "topic")
    source_oid = advance_source(repo, source)
    git(repo, "config", f"branch.{branch}.mergeOptions", "--squash")
    result = integrate_branch(repo)
    assert result.outcome == Outcome.UPDATED
    assert git(repo, "rev-parse", "HEAD^1") == original
    assert git(repo, "rev-parse", "HEAD^2") == source_oid
    assert git(repo, "status", "--porcelain") == ""


def test_dry_run_no_mutation(workflow) -> None:
    repo, remote = workflow
    advance_source(repo)
    before, server = contents(repo), contents(remote)
    result = CliRunner().invoke(app, ["integrate", "--path", str(repo), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "no fetch or mutation" in result.output
    assert contents(repo) == before
    assert contents(remote) == server


@pytest.mark.parametrize(
    "problem",
    [
        "dirty",
        "missing-source",
        "missing-upstream",
        "stale-source",
        "other-branch",
        "main",
        "operation",
    ],
)
def test_preconditions(workflow, problem: str) -> None:
    repo, remote = workflow
    if problem == "dirty":
        (repo / "work").write_text("user work")
    elif problem == "missing-source":
        git(repo, "branch", "-D", "develop")
    elif problem == "missing-upstream":
        git(repo, "config", "--unset", "branch.develop.remote")
    elif problem == "stale-source":
        git(repo, "checkout", "develop")
        commit_file(repo, "unpublished", "unpublished source")
        git(repo, "checkout", "feature/topic")
    elif problem == "main":
        git(repo, "checkout", "main")
    elif problem == "operation":
        (repo / ".git" / "MERGE_HEAD").write_text(git(repo, "rev-parse", "HEAD") + "\n")
    oid = git(repo, "rev-parse", "HEAD")
    server = contents(remote)
    result = integrate_branch(repo, "develop" if problem == "other-branch" else None)
    assert result.outcome == Outcome.BLOCKED
    assert git(repo, "rev-parse", "HEAD") == oid
    assert contents(remote) == server


@pytest.mark.parametrize("kind", ["feature", "hotfix"])
def test_conflicts_preserve_state(workflow, kind: str) -> None:
    repo, remote = workflow
    if kind == "hotfix":
        git(repo, "checkout", "-b", "hotfix/fix")
    original = commit_file(repo, "conflict", "topic version")
    advance_source(repo, "develop" if kind == "feature" else "main", "conflict")
    result = integrate_branch(repo)
    assert result.outcome == Outcome.BLOCKED
    assert "--continue" in result.recovery
    assert "--abort" in result.recovery
    assert git(repo, "ls-files", "--unmerged")
    assert "<<<<<<<" in (repo / "conflict").read_text()
    assert integrate_branch(repo).outcome == Outcome.BLOCKED
    git(repo, "rebase" if kind == "feature" else "merge", "--abort")
    assert git(repo, "rev-parse", "HEAD") == original


def test_override_and_changed_plan(workflow) -> None:
    repo, remote = workflow
    advance_source(repo, "main")
    plans = []
    assert (
        integrate_branch(repo, source="main", dry_run=True, show_plan=plans.append).outcome
        == Outcome.PLANNED
    )
    assert plans[-1].steps[0].source == "refs/heads/main"

    def change(plan):
        if any(step.action == Action.REBASE for step in plan.steps):
            git(repo, "commit", "--allow-empty", "-m", "concurrent")

    assert integrate_branch(repo, source="main", show_plan=change).outcome == Outcome.BLOCKED
