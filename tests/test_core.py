import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gitex.cli import app
from gitex.errors import GitError
from gitex.git import Relationship, assess_relationship, repository_root, resolve_commit


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "commit", "--allow-empty", "-m", "initial")
    return tmp_path


def contents(repo: Path) -> dict[str, bytes]:
    return {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


def test_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "GitEXtra" in result.stdout


def test_relationships_are_read_only(repo: Path) -> None:
    git(repo, "branch", "topic")
    initial = resolve_commit(repo, "main")
    assert assess_relationship(repo, "main", "topic").relationship == Relationship.IDENTICAL
    git(repo, "commit", "--allow-empty", "-m", "main change")
    before = contents(repo)
    ahead = assess_relationship(repo, "main", "topic")
    assert (ahead.relationship, ahead.ahead, ahead.behind) == (Relationship.AHEAD, 1, 0)
    assert ahead.source_oid == initial
    assert assess_relationship(repo, "topic", "main").relationship == Relationship.BEHIND
    assert contents(repo) == before
    git(repo, "checkout", "topic")
    git(repo, "commit", "--allow-empty", "-m", "topic change")
    (repo / "dirty.txt").write_text("user work")
    before = contents(repo)
    result = assess_relationship(repo, "main", "topic")
    assert (result.relationship, result.ahead, result.behind) == (Relationship.DIVERGED, 1, 1)
    assert repository_root(repo) == repo.resolve()
    assert contents(repo) == before


@pytest.mark.parametrize("ref", ["missing", "--help", "main~1", "main^{tree}", "bad name"])
def test_invalid_or_missing_ref(repo: Path, ref: str) -> None:
    before = contents(repo)
    with pytest.raises(GitError):
        resolve_commit(repo, ref)
    assert contents(repo) == before


def test_not_a_repository(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="Git inspection failed"):
        repository_root(tmp_path)


def test_git_output_preserves_undecodable_bytes(tmp_path: Path, monkeypatch) -> None:
    import sys

    from gitex.git import _run

    run = subprocess.run

    def emit_raw_output(args, **kwargs):
        return run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'?? file-\\xff\\x00')"],
            **kwargs,
        )

    monkeypatch.setattr(subprocess, "run", emit_raw_output)
    assert _run(tmp_path, "status").encode("utf-8", errors="surrogateescape") == b"?? file-\xff\x00"
