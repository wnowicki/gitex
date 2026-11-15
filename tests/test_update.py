from pathlib import Path

import pytest
from test_core import contents, git
from typer.testing import CliRunner

from gitex.cli import app
from gitex.commands.update import dependency_order, update_all, update_branch
from gitex.git import snapshot
from gitex.models import Outcome


@pytest.fixture
def workflow(repo: Path) -> tuple[Path, Path]:
    remote = repo.parent / (repo.name + "-remote.git")
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "branch", "develop")
    git(repo, "checkout", "-b", "feature/topic")
    git(repo, "push", "-u", "origin", "main", "develop", "feature/topic")
    return repo, remote


def commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-m", text)
    return git(repo, "rev-parse", "HEAD")


def source_change(repo: Path, file: str = "source") -> str:
    git(repo, "checkout", "develop")
    oid = commit(repo, file, "source change")
    git(repo, "push")
    git(repo, "checkout", "feature/topic")
    return oid


def test_update_rebases_and_publishes_unpublished_commits(workflow) -> None:
    repo, remote = workflow
    commit(repo, "topic", "topic")
    source = source_change(repo)
    result = update_branch(repo)
    assert result.outcome == Outcome.UPDATED
    assert result.local_applied and result.published
    assert git(repo, "rev-parse", "HEAD^") == source
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == git(repo, "rev-parse", "HEAD")
    assert (repo / "topic").read_text() == "topic"
    assert git(repo, "status", "--porcelain") == ""
    assert update_branch(repo).outcome == Outcome.UNCHANGED


@pytest.mark.parametrize("push", [True, False])
def test_published_rebase_keeps_local_success(workflow, push: bool) -> None:
    repo, remote = workflow
    published = commit(repo, "topic", "published")
    git(repo, "push")
    source = source_change(repo)
    result = update_branch(repo, push=push)
    assert result.local_applied and not result.published
    assert result.outcome == (Outcome.BLOCKED if push else Outcome.UPDATED)
    assert git(repo, "rev-parse", "HEAD^") == source
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == published


@pytest.mark.parametrize("all_branches", [True, False])
def test_dry_run_changes_nothing(workflow, all_branches: bool) -> None:
    repo, remote = workflow
    commit(repo, "topic", "topic")
    source_change(repo)
    before, server = contents(repo), contents(remote)
    args = ["update", "--path", str(repo), "--dry-run"] + (["--all"] if all_branches else [])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "Conditional" in result.output
    assert contents(repo) == before
    assert contents(remote) == server
    assert len(snapshot(repo).worktrees) == 1


@pytest.mark.parametrize("problem", ["dirty", "missing-upstream", "missing-source", "diverged"])
def test_update_blocks_unsafe_states(workflow, problem: str) -> None:
    repo, remote = workflow
    if problem == "dirty":
        (repo / "work").write_text("user work")
    elif problem == "missing-upstream":
        git(repo, "branch", "--unset-upstream")
    elif problem == "missing-source":
        git(repo, "branch", "-D", "develop")
    else:
        peer = repo.parent / (repo.name + "-peer")
        git(repo, "clone", "-b", "feature/topic", str(remote), str(peer))
        git(peer, "config", "user.name", "Test")
        git(peer, "config", "user.email", "test@example.invalid")
        commit(peer, "remote", "remote")
        git(peer, "push")
        commit(repo, "local", "local")
    original = git(repo, "rev-parse", "HEAD")
    server = contents(remote)
    assert update_branch(repo).outcome == Outcome.BLOCKED
    assert git(repo, "rev-parse", "HEAD") == original
    assert contents(remote) == server


def test_all_orders_dependencies_and_removes_success_worktrees(workflow) -> None:
    repo, remote = workflow
    git(repo, "checkout", "main")
    git(repo, "branch", "bugfix/fix")
    git(repo, "push", "-u", "origin", "bugfix/fix")
    order, _ = dependency_order(snapshot(repo))
    assert order.index("main") < order.index("develop") < order.index("feature/topic")
    head = git(repo, "rev-parse", "HEAD")
    results = update_all(repo)
    assert all(r.result.outcome == Outcome.UNCHANGED for r in results)
    assert git(repo, "branch", "--show-current") == "main"
    assert git(repo, "rev-parse", "HEAD") == head
    assert len(snapshot(repo).worktrees) == 1
    assert all(r.worktree is None for r in results)


def test_all_skips_dependents_and_continues_independent_branches(workflow) -> None:
    repo, remote = workflow
    git(repo, "checkout", "-b", "hotfix/fix", "main")
    git(repo, "push", "-u", "origin", "hotfix/fix")
    hotfix = commit(repo, "hotfix", "production fix")
    git(repo, "checkout", "develop")
    commit(repo, "dev", "unpublished develop")
    git(repo, "checkout", "feature/topic")
    results = {r.branch: r for r in update_all(repo)}
    assert results["develop"].result.outcome == Outcome.BLOCKED
    assert results["feature/topic"].result.outcome == Outcome.SKIPPED
    assert results["hotfix/fix"].result.published
    assert git(remote, "rev-parse", "refs/heads/hotfix/fix") == hotfix
    assert git(repo, "branch", "--show-current") == "feature/topic"
    # Clean but blocked branch worktrees are preserved for explicit inspection.
    preserved = results["develop"].worktree
    assert preserved is not None
    git(repo, "worktree", "remove", str(preserved))


def test_all_does_not_modify_other_worktrees(workflow) -> None:
    repo, remote = workflow
    other = repo.parent / (repo.name + "-other")
    git(repo, "worktree", "add", str(other), "develop")
    before = contents(other)
    results = {r.branch: r for r in update_all(repo)}
    assert results["develop"].result.outcome == Outcome.BLOCKED
    assert results["feature/topic"].result.outcome == Outcome.SKIPPED
    assert contents(other) == before
    git(repo, "worktree", "remove", str(other))


def test_all_preserves_conflict_worktree(workflow) -> None:
    repo, remote = workflow
    commit(repo, "conflict", "topic")
    source_change(repo, "conflict")
    git(repo, "checkout", "main")
    original = git(repo, "rev-parse", "HEAD")
    results = {r.branch: r for r in update_all(repo)}
    topic = results["feature/topic"]
    assert topic.result.outcome == Outcome.BLOCKED
    assert topic.worktree is not None
    assert git(topic.worktree, "ls-files", "--unmerged")
    assert git(repo, "rev-parse", "HEAD") == original
    assert git(repo, "branch", "--show-current") == "main"
    git(topic.worktree, "rebase", "--abort")
    git(repo, "worktree", "remove", str(topic.worktree))


def test_cli_rejects_all_with_name(workflow) -> None:
    repo, remote = workflow
    result = CliRunner().invoke(app, ["update", "feature/topic", "--all", "--path", str(repo)])
    assert result.exit_code == 2


def test_rejected_publication_keeps_applied_rebase(workflow) -> None:
    repo, remote = workflow
    commit(repo, "topic", "topic")
    source = source_change(repo)
    old = git(remote, "rev-parse", "refs/heads/feature/topic")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    result = update_branch(repo)
    assert result.outcome == Outcome.BLOCKED
    assert result.local_applied and not result.published
    assert git(repo, "rev-parse", "HEAD^") == source
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == old
    hook.write_text("#!/bin/sh\nexit 0\n")
    assert update_branch(repo).published


def test_single_update_preserves_conflict_and_partial_sync(workflow) -> None:
    repo, remote = workflow
    commit(repo, "conflict", "topic")
    source_change(repo, "conflict")
    result = update_branch(repo)
    assert result.outcome == Outcome.BLOCKED
    assert not result.published
    assert git(repo, "ls-files", "--unmerged")
    assert "--continue" in result.recovery
    git(repo, "rebase", "--abort")


def test_all_excludes_remote_only_and_unknown_branches(workflow) -> None:
    repo, remote = workflow
    git(repo, "branch", "notes")
    git(repo, "branch", "feature/remote-only")
    git(repo, "push", "origin", "feature/remote-only")
    git(repo, "branch", "-D", "feature/remote-only")
    results = update_all(repo, dry_run=True)
    assert "notes" not in {r.branch for r in results}
    assert "feature/remote-only" not in {r.branch for r in results}


def test_cleanup_preserves_ignored_local_data(workflow) -> None:
    repo, remote = workflow
    git(repo, "checkout", "main")

    # A post-plan callback emulates user-created data in the managed checkout.
    def leave_data(plan):
        for step in plan.steps:
            if step.action.value == "worktree-remove":
                worktree = Path(step.target)
                info = Path(git(worktree, "rev-parse", "--git-path", "info/exclude"))
                info.write_text("private\n")
                (worktree / "private").write_text("preserve me")

    results = update_all(repo, show_plan=leave_data)
    preserved = [r.worktree for r in results if r.worktree]
    assert preserved
    for worktree in preserved:
        assert (worktree / "private").read_text() == "preserve me"
        (worktree / "private").unlink()
        git(repo, "worktree", "remove", str(worktree))


def test_cli_no_push_and_noninteractive_conflict_status(workflow) -> None:
    repo, remote = workflow
    commit(repo, "topic", "published")
    git(repo, "push")
    source_change(repo)
    server = contents(remote)
    result = CliRunner().invoke(app, ["update", "--path", str(repo), "--no-push"])
    assert result.exit_code == 0, result.output
    assert "publication disabled" in result.output
    assert contents(remote) == server


def test_all_cli_reports_preserved_worktree_and_nonzero_status(workflow) -> None:
    repo, remote = workflow
    git(repo, "checkout", "develop")
    commit(repo, "dev", "requires review")
    git(repo, "checkout", "feature/topic")
    result = CliRunner().invoke(app, ["update", "--path", str(repo), "--all"])
    assert result.exit_code == 1
    assert "Worktree preserved" in result.output
    assert "skipped" in result.output
    for worktree in snapshot(repo).worktrees:
        if worktree.path != repo:
            git(repo, "worktree", "remove", str(worktree.path))


def test_update_preserves_local_success_with_incoming_invalid_config(workflow) -> None:
    repo, remote = workflow
    git(repo, "checkout", "develop")
    source = commit(repo, ".gitex.toml", "[workflow]\npush_on_sync='invalid'\n")
    git(repo, "push")
    git(repo, "checkout", "feature/topic")
    old = git(remote, "rev-parse", "refs/heads/feature/topic")
    result = update_branch(repo)
    assert result.outcome == Outcome.FAILED
    assert result.local_applied and not result.published
    assert "Local changes applied" in result.reason
    assert git(repo, "rev-parse", "HEAD") == source
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == old
