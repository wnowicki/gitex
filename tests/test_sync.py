from pathlib import Path

import pytest
from test_core import contents, git
from typer.testing import CliRunner

from gitex.cli import app
from gitex.commands.sync import sync_branch
from gitex.models import Action, Outcome


@pytest.fixture
def tracked(repo: Path, tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = tmp_path.parent / (tmp_path.name + "-remote.git")
    peer = tmp_path.parent / (tmp_path.name + "-peer")
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "checkout", "-b", "feature/topic")
    git(repo, "push", "-u", "origin", "feature/topic")
    git(repo, "clone", "-b", "feature/topic", str(remote), str(peer))
    git(peer, "config", "user.name", "Test")
    git(peer, "config", "user.email", "test@example.invalid")
    return repo, remote, peer


def advance(repo: Path, message: str = "change") -> str:
    (repo / "file").write_text(message)
    git(repo, "add", "file")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def test_fast_forward_and_repeat(tracked) -> None:
    repo, remote, peer = tracked
    oid = advance(peer)
    git(peer, "push")
    plans = []
    result = sync_branch(repo, show_plan=plans.append)
    assert result.outcome == Outcome.UPDATED
    assert result.local_applied and not result.published
    assert git(repo, "rev-parse", "HEAD") == oid
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == oid
    assert (repo / "file").read_text() == "change"
    assert git(repo, "status", "--porcelain") == ""
    assert plans[0].steps[0].action == Action.FETCH
    assert plans[1].steps[0].action == Action.FAST_FORWARD
    assert sync_branch(repo).outcome == Outcome.UNCHANGED


def test_push_and_no_push(tracked) -> None:
    repo, remote, peer = tracked
    old = git(remote, "rev-parse", "refs/heads/feature/topic")
    oid = advance(repo)
    assert sync_branch(repo, push=False).outcome == Outcome.UNCHANGED
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == old
    result = sync_branch(repo)
    assert result.published
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == oid


def test_dry_run_preserves_everything(tracked) -> None:
    repo, remote, peer = tracked
    advance(repo)
    advance(peer, "unfetched")
    git(peer, "push")
    before, server = contents(repo), contents(remote)
    result = sync_branch(repo, dry_run=True)
    assert result.outcome == Outcome.PLANNED
    assert contents(repo) == before
    assert contents(remote) == server
    cli = CliRunner().invoke(app, ["sync", "--path", str(repo), "--dry-run"])
    assert cli.exit_code == 0
    assert "no fetch or mutation" in cli.output
    assert contents(repo) == before
    assert contents(remote) == server


@pytest.mark.parametrize(
    "problem", ["dirty", "staged", "operation", "missing-upstream", "diverged", "other-branch"]
)
def test_blocks_unsafe_states(tracked, problem: str) -> None:
    repo, remote, peer = tracked
    if problem in ("dirty", "staged"):
        (repo / "work").write_text("user work")
        if problem == "staged":
            git(repo, "add", "work")
    elif problem == "operation":
        (repo / ".git" / "MERGE_HEAD").write_text(git(repo, "rev-parse", "HEAD") + "\n")
    elif problem == "missing-upstream":
        git(repo, "branch", "--unset-upstream")
    elif problem == "diverged":
        advance(repo, "local")
        advance(peer, "remote")
        git(peer, "push")
    local_oid = git(repo, "rev-parse", "HEAD")
    server = contents(remote)
    result = sync_branch(repo, "main" if problem == "other-branch" else None)
    assert result.outcome == Outcome.BLOCKED
    assert git(repo, "rev-parse", "HEAD") == local_oid
    assert contents(remote) == server


def test_rejected_push_and_retry(tracked) -> None:
    repo, remote, peer = tracked
    old = git(remote, "rev-parse", "refs/heads/feature/topic")
    oid = advance(repo)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    result = sync_branch(repo)
    assert result.outcome == Outcome.BLOCKED
    assert not result.published
    assert git(repo, "rev-parse", "HEAD") == oid
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == old
    hook.write_text("#!/bin/sh\nexit 0\n")
    assert sync_branch(repo).published


def test_base_publication_blocked(tracked) -> None:
    repo, remote, peer = tracked
    git(repo, "checkout", "-b", "develop")
    git(repo, "push", "-u", "origin", "develop")
    old = git(remote, "rev-parse", "refs/heads/develop")
    advance(repo)
    assert sync_branch(repo).outcome == Outcome.BLOCKED
    assert git(remote, "rev-parse", "refs/heads/develop") == old
    assert sync_branch(repo, push=False).outcome == Outcome.UNCHANGED


def test_post_plan_oid_change_blocks_mutation(tracked) -> None:
    repo, remote, peer = tracked
    advance(repo)
    old = git(remote, "rev-parse", "refs/heads/feature/topic")

    def change(plan):
        if any(step.action == Action.PUSH for step in plan.steps):
            advance(repo, "concurrent change")

    assert sync_branch(repo, show_plan=change).outcome == Outcome.BLOCKED
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == old


def test_sync_does_not_follow_unsafe_fetch_or_push_config(tracked) -> None:
    repo, remote, peer = tracked
    git(repo, "branch", "mirror/main")
    mirror = git(repo, "rev-parse", "mirror/main")
    # Multiple fetch mappings still include the valid tracked upstream.
    git(repo, "config", "--add", "remote.origin.fetch", "+refs/heads/*:refs/heads/mirror/*")
    git(repo, "config", "remote.origin.push", "+refs/heads/*:refs/heads/*")
    git(repo, "tag", "do-not-publish")
    oid = advance(repo)
    assert sync_branch(repo).published
    assert git(repo, "rev-parse", "mirror/main") == mirror
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == oid
    assert git(remote, "tag") == ""
    assert (
        git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/")
        == "refs/heads/feature/topic"
    )


def test_fetch_failure_and_deleted_upstream(tracked) -> None:
    repo, remote, peer = tracked
    git(remote, "update-ref", "-d", "refs/heads/feature/topic")
    oid = git(repo, "rev-parse", "HEAD")
    result = sync_branch(repo)
    assert result.outcome == Outcome.BLOCKED
    assert not result.local_applied
    assert git(repo, "rev-parse", "HEAD") == oid


def test_other_worktree_is_not_modified(tracked) -> None:
    repo, remote, peer = tracked
    linked = repo.parent / (repo.name + "-linked")
    git(repo, "worktree", "add", "-b", "feature/other", str(linked))
    before = contents(linked)
    assert sync_branch(repo, "feature/other").outcome == Outcome.BLOCKED
    assert contents(linked) == before


def test_config_no_push_and_cli_override(tracked) -> None:
    repo, remote, peer = tracked
    (repo / ".gitex.toml").write_text("[workflow]\npush_on_sync=false\n")
    git(repo, "add", ".gitex.toml")
    git(repo, "commit", "-m", "config")
    old = git(remote, "rev-parse", "refs/heads/feature/topic")
    assert sync_branch(repo).outcome == Outcome.UNCHANGED
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == old
    result = CliRunner().invoke(app, ["sync", "--path", str(repo), "--push"])
    assert result.exit_code == 0, result.output
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == git(repo, "rev-parse", "HEAD")


def test_remote_race_cannot_force_publication(tracked) -> None:
    repo, remote, peer = tracked
    local = advance(repo, "local")
    remote_oid = None

    def race(plan):
        nonlocal remote_oid
        if any(step.action == Action.PUSH for step in plan.steps):
            remote_oid = advance(peer, "racing remote")
            git(peer, "push")

    result = sync_branch(repo, show_plan=race)
    assert result.outcome == Outcome.BLOCKED
    assert not result.published
    assert git(repo, "rev-parse", "HEAD") == local
    assert git(remote, "rev-parse", "refs/heads/feature/topic") == remote_oid
