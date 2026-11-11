from pathlib import Path

import pytest
from test_core import contents, git

from gitex.branches import BranchType, Strategy, branch_policy
from gitex.config import load_config
from gitex.errors import ConfigError
from gitex.git import snapshot
from gitex.models import RemoteFreshness


def test_config_defaults_and_precedence(tmp_path: Path) -> None:
    assert load_config(tmp_path).workflow.push_on_sync is True
    (tmp_path / ".gitex.toml").write_text(
        '[branches]\nmain="production"\n[workflow]\npush_on_sync=false\n'
    )
    config = load_config(tmp_path, {"branches": {"main": "stable", "develop": None}})
    assert config.branches.main == "stable"
    assert config.branches.develop == "develop"
    assert config.workflow.push_on_sync is False


@pytest.mark.parametrize(
    "text",
    [
        "[bad]\nx=1",
        "[branches]\nunknown=1",
        '[branches]\nmain="bad name"',
        '[branches]\nmain="develop"',
        '[branches]\nfeature_prefix="feature"',
        '[branches]\nbugfix_prefix="feature/sub/"',
        '[branches]\nmain="feature/base"',
        '[workflow]\npush_on_sync="true"',
        '[workflow]\nfinish_method="merge"',
        '[release]\nchangelog="../other.md"',
        '[release]\ntag_prefix="bad prefix"',
        'branches="main"',
        "[broken",
    ],
)
def test_config_rejects_invalid(tmp_path: Path, text: str) -> None:
    (tmp_path / ".gitex.toml").write_text(text)
    before = contents(tmp_path)
    with pytest.raises(ConfigError):
        load_config(tmp_path)
    assert contents(tmp_path) == before


@pytest.mark.parametrize(
    "name,kind,creation,source,target,strategy",
    [
        ("main", BranchType.MAIN, None, None, None, None),
        ("develop", BranchType.DEVELOP, "main", "main", None, Strategy.MERGE),
        ("feature/one", BranchType.FEATURE, "develop", "develop", "develop", Strategy.REBASE),
        ("bugfix/one", BranchType.BUGFIX, "develop", "develop", "develop", Strategy.REBASE),
        ("release/v1", BranchType.RELEASE, "develop", "main", "main", Strategy.MERGE),
        ("hotfix/v1", BranchType.HOTFIX, "main", "main", "main", Strategy.MERGE),
        ("other", BranchType.UNKNOWN, None, None, None, None),
        ("feature/", BranchType.UNKNOWN, None, None, None, None),
    ],
)
def test_policies(name, kind, creation, source, target, strategy) -> None:
    policy = branch_policy(name)
    assert (
        policy.kind,
        policy.creation_source,
        policy.integration_source,
        policy.delivery_target,
        policy.strategy,
    ) == (kind, creation, source, target, strategy)


def test_custom_policy(tmp_path: Path) -> None:
    config = load_config(
        tmp_path, {"branches": {"main": "stable", "develop": "dev", "feature_prefix": "feat/"}}
    )
    assert branch_policy("feat/topic", config.branches).integration_source == "dev"
    assert branch_policy("hotfix/fix", config.branches).creation_source == "stable"
    assert branch_policy("main", config.branches).kind == BranchType.UNKNOWN


def test_snapshot_dirty_rename_and_worktree(repo: Path, tmp_path: Path) -> None:
    (repo / "old.txt").write_text("original")
    git(repo, "add", "old.txt")
    git(repo, "commit", "-m", "file")
    git(repo, "mv", "old.txt", "new name.txt")
    (repo / "new name.txt").write_text("edited")
    (repo / " untracked\nfile").write_text("user work")
    linked = tmp_path / "linked worktree"
    git(repo, "worktree", "add", "-b", "feature/linked", str(linked))
    before = contents(repo)
    state = snapshot(repo)
    assert state.dirty
    assert state.head_ref == "refs/heads/main"
    assert state.head_oid == git(repo, "rev-parse", "HEAD")
    assert state.remote_freshness == RemoteFreshness.UNKNOWN
    rename = next(f for f in state.files if f.path == "new name.txt")
    assert (rename.index, rename.worktree, rename.original_path) == ("R", "M", "old.txt")
    assert any(f.path == " untracked\nfile" for f in state.files)
    assert any(
        w.branch == "refs/heads/feature/linked" and w.path == linked for w in state.worktrees
    )
    assert contents(repo) == before
    linked_before = contents(linked)
    assert snapshot(linked).head_ref == "refs/heads/feature/linked"
    assert contents(linked) == linked_before


def test_unborn_and_detached(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "main")
    before = contents(tmp_path)
    state = snapshot(tmp_path)
    assert state.head_oid is None
    assert state.head_ref == "refs/heads/main"
    assert state.refs == ()
    assert contents(tmp_path) == before
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "commit", "--allow-empty", "-m", "initial")
    git(tmp_path, "checkout", "--detach")
    state = snapshot(tmp_path)
    assert state.head_ref is None
    assert state.head_oid == git(tmp_path, "rev-parse", "HEAD")


def test_tracking_and_cached_remote(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    git(repo, "commit", "--allow-empty", "-m", "local")
    git(repo, "branch", "local-only")
    before, remote_before = contents(repo), contents(remote)
    state = snapshot(repo)
    main = next(r for r in state.refs if r.name == "refs/heads/main")
    assert (main.upstream, main.remote, main.remote_ref, main.ahead, main.behind) == (
        "refs/remotes/origin/main",
        "origin",
        "refs/heads/main",
        1,
        0,
    )
    assert main.upstream_oid == git(repo, "rev-parse", "origin/main")
    assert next(r for r in state.refs if r.name == "refs/heads/local-only").upstream is None
    assert contents(repo) == before
    assert contents(remote) == remote_before
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    main = next(r for r in snapshot(repo).refs if r.name == "refs/heads/main")
    assert main.upstream == "refs/remotes/origin/main"
    assert main.upstream_oid is None
    assert main.ahead is None


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_conflicted_operation(repo: Path, operation: str) -> None:
    (repo / "file").write_text("base")
    git(repo, "add", "file")
    git(repo, "commit", "-m", "base")
    git(repo, "checkout", "-b", "feature/topic")
    (repo / "file").write_text("topic")
    git(repo, "commit", "-am", "topic")
    git(repo, "checkout", "main")
    (repo / "file").write_text("main")
    git(repo, "commit", "-am", "main")
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repo), operation, "feature/topic"], capture_output=True
    )
    assert result.returncode == 1
    before = contents(repo)
    state = snapshot(repo)
    assert operation in state.operations
    assert any(f.index == "U" and f.worktree == "U" for f in state.files)
    assert contents(repo) == before


@pytest.mark.parametrize("mode,expected", [("behind", (0, 1)), ("diverged", (1, 1))])
def test_snapshot_tracking_divergence(
    repo: Path, tmp_path: Path, mode: str, expected: tuple[int, int]
) -> None:
    remote = tmp_path / "bare.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    peer = tmp_path / "peer"
    git(repo, "clone", "-b", "main", str(remote), str(peer))
    git(peer, "config", "user.name", "Test")
    git(peer, "config", "user.email", "test@example.invalid")
    git(peer, "commit", "--allow-empty", "-m", "remote")
    git(peer, "push")
    if mode == "diverged":
        git(repo, "commit", "--allow-empty", "-m", "local")
    git(repo, "fetch")
    before, remote_before = contents(repo), contents(remote)
    main = next(r for r in snapshot(repo).refs if r.name == "refs/heads/main")
    assert (main.ahead, main.behind) == expected
    assert contents(repo) == before
    assert contents(remote) == remote_before
