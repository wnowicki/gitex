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
    report = list_branches(repo, refresh=False)
    assert contents(repo) == before
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
    assert "Fetched with prune" in result.output
    assert "feature/topic" in result.output
    assert "dirty" in result.output
    assert (repo / "user work").read_text() == "untouched"
    assert git(repo, "rev-parse", "HEAD") == topic.ref.oid
    assert contents(remote) == remote_before


def test_missing_source_and_upstream(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "bare.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "checkout", "-b", "feature/topic")
    git(repo, "push", "-u", "origin", "feature/topic")
    git(repo, "update-ref", "-d", "refs/remotes/origin/feature/topic")
    topic = next(
        b for b in list_branches(repo, refresh=False).branches if b.name == "feature/topic"
    )
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
    report = list_branches(repo, refresh=False)
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
    branch = next(b for b in list_branches(repo, refresh=False).branches if b.name == "release/v1")
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
    report = list_branches(repo, refresh=False)
    stream = StringIO()
    render_list(report, Console(file=stream, width=120, force_terminal=True))
    output = stream.getvalue()
    assert "\x1b[" in output
    assert "feature/red]" in output
    assert "Last commit" in output
    assert "Worktrees" not in output
    assert "Upstream / tracking" not in output


def test_unknown_parent_defaults_to_configured_develop(repo: Path, tmp_path: Path) -> None:
    from io import StringIO

    from rich.console import Console

    from gitex.presentation import render_list

    (repo / ".gitex.toml").write_text('[branches]\ndevelop="dev"')
    git(repo, "branch", "dev")
    git(repo, "branch", "custom")
    remote = tmp_path / "bare.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "dev", "custom:remote-custom")
    before = contents(repo)
    report = list_branches(repo, refresh=False)
    custom = next(b for b in report.branches if b.name == "custom")
    assert custom.parent_inferred
    assert custom.source_ref == "refs/heads/dev"
    assert custom.relationship.relationship == Relationship.IDENTICAL
    assert custom.policy.delivery_target is None
    remote_custom = next(b for b in report.branches if b.name == "remote-custom")
    assert remote_custom.source_ref == "refs/remotes/origin/dev"
    assert remote_custom.parent_inferred
    assert not next(b for b in report.branches if b.name == "main").parent_inferred
    stream = StringIO()
    render_list(report, Console(file=stream, width=160, force_terminal=True, no_color=False))
    import re

    assert re.search(r"\x1b\[35m\s*dev", stream.getvalue())
    assert contents(repo) == before


def test_list_fetches_new_refs_and_prunes_deleted_refs(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "bare.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "branch", "obsolete")
    git(repo, "push", "-u", "origin", "main", "obsolete")
    git(repo, "branch", "-D", "obsolete")
    # Change the server directly: the local remote-tracking knowledge is stale.
    oid = git(repo, "rev-parse", "HEAD")
    git(remote, "update-ref", "-d", "refs/heads/obsolete")
    git(remote, "update-ref", "refs/heads/new-remote", oid)
    (repo / "user work").write_text("preserved")
    index = (repo / ".git" / "index").read_bytes() if (repo / ".git" / "index").exists() else None
    server_before = contents(remote)
    report = list_branches(repo)
    assert report.fetched
    assert not any(b.name == "obsolete" for b in report.branches)
    assert any(b.name == "new-remote" for b in report.branches)
    assert git(repo, "rev-parse", "refs/remotes/origin/new-remote") == oid
    assert git(repo, "rev-parse", "HEAD") == oid
    assert (repo / "user work").read_text() == "preserved"
    assert (
        (repo / ".git" / "index").read_bytes() if (repo / ".git" / "index").exists() else None
    ) == index
    assert contents(remote) == server_before


def test_fetch_failure_stops_list(repo: Path, tmp_path: Path) -> None:
    git(repo, "remote", "add", "origin", str(tmp_path / "missing.git"))
    result = CliRunner().invoke(app, ["list", str(repo)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Last commit" not in result.output


def test_list_orders_full_commit_dates(repo: Path, monkeypatch) -> None:
    git(repo, "checkout", "-b", "newer")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-11-10T09:00:00+00:00")
    git(repo, "commit", "--allow-empty", "-m", "newer")
    git(repo, "checkout", "-b", "older")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-11-10T10:00:00+02:00")
    git(repo, "commit", "--allow-empty", "-m", "older")
    names = [b.name for b in list_branches(repo).branches]
    assert names.index("newer") < names.index("older")
