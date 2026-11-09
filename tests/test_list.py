from pathlib import Path

from test_core import contents, git
from typer.testing import CliRunner

from gitex.branches import BranchType
from gitex.cli import app
from gitex.commands.list import list_branches
from gitex.git import Relationship


def test_list_branches_and_no_mutation(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "branch", "develop")
    git(repo, "branch", "feature/topic")
    git(repo, "branch", "bugfix/fix")
    git(repo, "branch", "release/v1")
    git(repo, "branch", "hotfix/v1")
    git(repo, "branch", "misc")
    git(repo, "push", "-u", "origin", "--all")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git(repo, "checkout", "develop")
    git(repo, "commit", "--allow-empty", "-m", "development")
    git(repo, "checkout", "feature/topic")
    git(repo, "commit", "--allow-empty", "-m", "topic")
    (repo / "user work").write_text("untouched")
    before, remote_before = contents(repo), contents(remote)
    report = list_branches(repo)
    local = {b.name: b for b in report.branches if b.local}
    assert {b.policy.kind for b in local.values()} == set(BranchType)
    topic = local["feature/topic"]
    assert topic.current
    assert topic.relationship.relationship == Relationship.DIVERGED
    assert topic.ref.ahead == 1
    assert topic.ref.behind == 0
    assert topic.readiness == "rebase needed; conflicts possible"
    assert topic.worktrees == (repo,)
    assert local["bugfix/fix"].readiness == "fast-forward possible"
    assert local["release/v1"].source_ref == "refs/heads/main"
    remote_topic = next(
        b for b in report.branches if b.ref.name == "refs/remotes/origin/feature/topic"
    )
    assert remote_topic.source_ref == "refs/remotes/origin/develop"
    assert remote_topic.relationship.relationship == Relationship.IDENTICAL
    assert not any(b.ref.symbolic_target for b in report.branches)
    result = CliRunner().invoke(app, ["list", str(repo)], terminal_width=240)
    assert result.exit_code == 0, result.output
    assert "cached refs may be stale" in result.output
    assert "feature/topic" in result.output
    assert "dirty" in result.output
    assert contents(repo) == before
    assert contents(remote) == remote_before


def test_missing_source_and_upstream(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "bare.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "checkout", "-b", "feature/topic")
    git(repo, "push", "-u", "origin", "feature/topic")
    git(repo, "update-ref", "-d", "refs/remotes/origin/feature/topic")
    topic = next(b for b in list_branches(repo).branches if b.name == "feature/topic")
    assert topic.readiness == "source missing"
    assert topic.relationship is None
    assert topic.ref.upstream_oid is None
    output = CliRunner().invoke(app, ["list", str(repo)], terminal_width=240)
    assert output.exit_code == 0
    assert "missing" in output.output


def test_unborn_detached_and_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["list", str(tmp_path)]).exit_code == 1
    git(tmp_path, "init", "-b", "main")
    result = runner.invoke(app, ["list", str(tmp_path)])
    assert result.exit_code == 0
    assert "unborn" in result.output
    assert "No branches found" in result.output
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "commit", "--allow-empty", "-m", "initial")
    git(tmp_path, "checkout", "--detach")
    assert "detached" in runner.invoke(app, ["list", str(tmp_path)]).output
    (tmp_path / ".gitex.toml").write_text('[workflow]\npush_on_sync="wrong"')
    result = runner.invoke(app, ["list", str(tmp_path)])
    assert result.exit_code == 1
    assert "Invalid type" in result.output


def test_custom_policy_and_remote_only(repo: Path, tmp_path: Path) -> None:
    (repo / ".gitex.toml").write_text('[branches]\ndevelop="dev"\nfeature_prefix="feat/"')
    git(repo, "branch", "dev")
    git(repo, "branch", "feat/custom")
    remote = tmp_path / "bare.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "team/remote", str(remote))
    git(repo, "push", "team/remote", "dev", "feat/custom")
    git(repo, "branch", "-D", "feat/custom")
    before = contents(repo)
    report = list_branches(repo)
    topic = next(b for b in report.branches if b.name == "feat/custom")
    assert not topic.local
    assert topic.policy.kind == BranchType.FEATURE
    assert topic.source_ref == "refs/remotes/team/remote/dev"
    assert contents(repo) == before
