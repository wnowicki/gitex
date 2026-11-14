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


@pytest.mark.parametrize("collision", ["file", "directory", "ancestor-file", "empty-directory"])
def test_rebase_preserves_ignored_collisions(workflow, collision: str) -> None:
    repo, remote = workflow
    # Excludes apply to both branches; force-add the incoming tracked path.
    (repo / ".git" / "info" / "exclude").write_text("private\n")
    git(repo, "checkout", "develop")
    if collision == "ancestor-file":
        (repo / "private").mkdir()
        (repo / "private" / "incoming").write_text("upstream file")
    else:
        (repo / "private").write_text("upstream file")
    git(repo, "add", "-f", "private")
    git(repo, "commit", "-m", "track incoming path")
    git(repo, "push")
    git(repo, "checkout", "feature/topic")
    if collision in ("directory", "empty-directory"):
        (repo / "private").mkdir()
        if collision == "directory":
            (repo / "private" / "local secret").write_bytes(b"must survive\x00\xff")
    else:
        (repo / "private").write_bytes(b"must survive\x00\xff")
    head = git(repo, "rev-parse", "HEAD")
    index = (repo / ".git" / "index").read_bytes()
    before = {p: data for p, data in contents(repo).items() if not p.startswith(".git/")}
    server = contents(remote)
    for dry_run in (True, False):
        result = integrate_branch(repo, dry_run=dry_run)
        assert result.outcome == Outcome.BLOCKED
        assert "Ignored local path collides" in result.reason
        assert git(repo, "rev-parse", "HEAD") == head
        assert (repo / ".git" / "index").read_bytes() == index
        assert {
            p: data for p, data in contents(repo).items() if not p.startswith(".git/")
        } == before
        assert (repo / "private").exists()
        assert not (repo / ".git" / "rebase-merge").exists()
        assert not (repo / ".git" / "rebase-apply").exists()
        assert contents(remote) == server


def test_rebase_allows_unrelated_ignored_file(workflow) -> None:
    repo, remote = workflow
    (repo / ".git" / "info" / "exclude").write_text("private\n")
    advance_source(repo)
    (repo / "private").write_text("keep this")
    assert integrate_branch(repo).outcome == Outcome.UPDATED
    assert (repo / "private").read_text() == "keep this"


@pytest.mark.parametrize("failure", ["git", "config", "file-read"])
def test_post_integration_failure_preserves_applied_result(
    workflow, monkeypatch, failure: str
) -> None:
    import importlib

    from gitex.errors import ConfigError, GitError

    module = importlib.import_module("gitex.commands.integrate")
    repo, remote = workflow
    source_oid = advance_source(repo)
    original_integrate = module.integrate_commit
    original_snapshot = module.snapshot
    applied = False

    def apply(*args, **kwargs):
        nonlocal applied
        original_integrate(*args, **kwargs)
        applied = True

    def inspect(*args, **kwargs):
        if applied:
            error = {"git": GitError, "config": ConfigError, "file-read": OSError}[failure]
            raise error("post-operation inspection unavailable")
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(module, "integrate_commit", apply)
    monkeypatch.setattr(module, "snapshot", inspect)
    server = contents(remote)
    result = module.integrate_branch(repo)
    assert result.outcome == Outcome.FAILED
    assert result.local_applied and not result.published
    assert "Locally integrated" in result.reason
    assert "post-operation inspection unavailable" in result.reason
    assert "--abort" not in result.recovery
    assert git(repo, "rev-parse", "HEAD") == source_oid
    assert git(repo, "status", "--porcelain") == ""
    assert contents(remote) == server


@pytest.mark.parametrize("via_cli", [False, True])
def test_incoming_invalid_config_reports_applied_integration(workflow, via_cli: bool) -> None:
    repo, remote = workflow
    git(repo, "checkout", "develop")
    source_oid = commit_file(repo, ".gitex.toml", "[workflow]\npush_on_sync = 'invalid'\n")
    git(repo, "push")
    git(repo, "checkout", "feature/topic")
    server = contents(remote)
    if via_cli:
        result = CliRunner().invoke(app, ["integrate", "--path", str(repo)])
        assert result.exit_code == 1
        assert "Locally integrated" in result.output
        assert "post-integration verification failed" in result.output
    else:
        result = integrate_branch(repo)
        assert result.outcome == Outcome.FAILED
        assert result.local_applied and not result.published
        assert "Invalid type" in result.reason
    assert git(repo, "rev-parse", "HEAD") == source_oid
    assert (repo / ".gitex.toml").read_text() == "[workflow]\npush_on_sync = 'invalid'\n"
    assert git(repo, "status", "--porcelain") == ""
    assert contents(remote) == server
