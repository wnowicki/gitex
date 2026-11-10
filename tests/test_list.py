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
    assert topic.readiness == "conflicts possible"
    assert topic.worktrees == (repo,)
    assert local["bugfix/fix"].readiness == "merged"
    assert local["release/v1"].source_ref == "refs/heads/main"
    assert topic.remote_refs == ("refs/remotes/origin/feature/topic",)
    assert topic.remote_differs
    assert topic.last_commit == git(repo, "show", "-s", "--format=%cI", topic.ref.oid)
    assert len(report.branches) == len(local)
    assert not any(b.ref.symbolic_target for b in report.branches)
    result = CliRunner().invoke(app, ["list", str(repo)], terminal_width=240)
    assert result.exit_code == 0, result.output
    assert "Cached refs may be stale" in result.output
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
    assert topic.readiness == "parent missing"
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


def test_release_compares_delivery_target_and_dates(repo: Path) -> None:
    git(repo, "branch", "develop")
    git(repo, "checkout", "-b", "release/v1")
    git(repo, "commit", "--allow-empty", "-m", "release change")
    before = contents(repo)
    branch = next(b for b in list_branches(repo).branches if b.name == "release/v1")
    assert branch.source_ref == "refs/heads/main"
    assert (branch.relationship.behind, branch.relationship.ahead) == (0, 1)
    assert branch.readiness == "ready (fast-forward)"
    assert branch.last_commit == git(repo, "show", "-s", "--format=%cI", "HEAD")
    assert contents(repo) == before


def test_compact_table_colours_and_literal_names(repo: Path) -> None:
    from io import StringIO

    from rich.console import Console

    from gitex.presentation import render_list

    git(repo, "branch", "feature/red]")
    report = list_branches(repo)
    stream = StringIO()
    render_list(report, Console(file=stream, width=120, force_terminal=True))
    output = stream.getvalue()
    assert "\x1b[" in output
    assert "feature/red]" in output
    assert "Last commit" in output
    assert "Worktrees" not in output
    assert "Upstream / tracking" not in output
