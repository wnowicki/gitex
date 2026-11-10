"""Read-only Git inspection using argument arrays and bounded execution."""

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gitex.config import load_config
from gitex.errors import GitError
from gitex.models import FileStatus, RefState, RepoSnapshot, WorktreeState


class Relationship(StrEnum):
    IDENTICAL = "identical"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"


@dataclass(frozen=True)
class RelationshipAssessment:
    target_oid: str
    source_oid: str
    relationship: Relationship
    ahead: int
    behind: int


def _run(repo: Path, *args: str) -> str:
    try:
        # Git is intentionally resolved on PATH; arguments are separate tokens.
        result = subprocess.run(  # noqa: S603
            ["git", "--no-optional-locks", "-C", str(repo), *args],  # noqa: S607
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise GitError("Git is unavailable; install Git and ensure it is on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("Git inspection timed out; check the repository and retry.") from exc
    except OSError as exc:
        raise GitError(f"Cannot run Git: {exc}") from exc
    if result.returncode:
        raise GitError(f"Git inspection failed: {result.stderr.strip()}")
    return result.stdout.rstrip("\n")


def repository_root(repo: Path) -> Path:
    """Resolve the working repository root or raise GitError."""
    return Path(_run(repo, "rev-parse", "--show-toplevel"))


def resolve_commit(repo: Path, ref: str) -> str:
    """Resolve a validated ref to a commit without accepting revision expressions."""
    if ref != "HEAD":
        _run(repo, "check-ref-format", "--allow-onelevel", ref)
        if ref.startswith("-"):
            raise GitError("Invalid ref; supply a branch name or full ref.")
    return _run(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")


def assess_relationship(repo: Path, target: str, source: str) -> RelationshipAssessment:
    """Compare pinned commits; divergence alone does not predict merge conflicts.

    No merge-tree --write-tree probe is used: inspection must not write objects.
    Remote-tracking refs reflect cached knowledge; this function never fetches.
    """
    target_oid = resolve_commit(repo, target)
    source_oid = resolve_commit(repo, source)
    return compare_commits(repo, target_oid, source_oid)


def compare_commits(repo: Path, target_oid: str, source_oid: str) -> RelationshipAssessment:
    """Compare pinned OIDs from inspection, avoiding later ref movement."""
    if any(
        not oid or any(c not in "0123456789abcdef" for c in oid) or len(oid) not in (40, 64)
        for oid in (target_oid, source_oid)
    ):
        raise GitError("Invalid commit OID; inspect the repository again.")
    counts = _run(repo, "rev-list", "--left-right", "--count", f"{target_oid}...{source_oid}")
    ahead, behind = map(int, counts.split())
    if not ahead and not behind:
        relationship = Relationship.IDENTICAL
    elif not behind:
        relationship = Relationship.AHEAD
    elif not ahead:
        relationship = Relationship.BEHIND
    else:
        relationship = Relationship.DIVERGED
    return RelationshipAssessment(target_oid, source_oid, relationship, ahead, behind)


def snapshot(repo: Path) -> RepoSnapshot:
    """Inspect cached repository state without fetching or refreshing the index.

    This is an observation, not an atomic transaction or permission to mutate.
    Consumers must revalidate critical OIDs before executing a later plan.
    """
    root = repository_root(repo)
    config = load_config(root)
    # symbolic-ref succeeds for unborn HEAD, while rev-parse HEAD does not.
    head_path = Path(_run(root, "rev-parse", "--path-format=absolute", "--git-path", "HEAD"))
    head_text = head_path.read_text().strip()
    head_ref = head_text[5:] if head_text.startswith("ref: ") else None
    raw_refs = _run(
        root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(symref)%00%(upstream)%00%(upstream:remotename)%00%(upstream:remoteref)",
        "refs/heads/",
        "refs/remotes/",
    )
    rows = [line.split("\0") for line in raw_refs.splitlines()]
    oids = {row[0]: row[1] for row in rows}
    refs = []
    for name, oid, symbolic, upstream, remote, remote_ref in rows:
        upstream_oid = oids.get(upstream)
        ahead = behind = None
        if upstream_oid:
            ahead, behind = map(
                int,
                _run(
                    root, "rev-list", "--left-right", "--count", f"{oid}...{upstream_oid}"
                ).split(),
            )
        refs.append(
            RefState(
                name,
                oid,
                symbolic or None,
                upstream or None,
                upstream_oid,
                remote or None,
                remote_ref or None,
                ahead,
                behind,
            )
        )
    head_oid = oids.get(head_ref) if head_ref else head_text
    status = _run(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"
    )
    files = []
    tokens = iter(status.split("\0"))
    for token in tokens:
        if not token:
            continue
        original = next(tokens) if "R" in token[:2] or "C" in token[:2] else None
        files.append(FileStatus(token[3:], token[0], token[1], original))
    operations = []
    for marker, operation in (
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase-or-am"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("sequencer", "sequencer"),
        ("BISECT_LOG", "bisect"),
    ):
        path = Path(_run(root, "rev-parse", "--path-format=absolute", "--git-path", marker))
        if path.exists():
            operations.append(operation)
    worktrees = []
    record = {}
    for token in _run(root, "worktree", "list", "--porcelain", "-z").split("\0"):
        if not token:
            if record:
                worktrees.append(
                    WorktreeState(
                        Path(record["worktree"]),
                        record.get("HEAD"),
                        record.get("branch"),
                        "bare" in record,
                        "locked" in record,
                        "prunable" in record,
                    )
                )
                record = {}
            continue
        key, _, value = token.partition(" ")
        record[key] = value
    return RepoSnapshot(
        root,
        head_oid,
        head_ref,
        tuple(files),
        tuple(operations),
        tuple(refs),
        tuple(worktrees),
        tuple(_run(root, "remote").splitlines()),
        config,
    )


def commit_date(repo: Path, oid: str) -> str:
    """Return the pinned tip's committer date in ISO 8601 format."""
    if len(oid) not in (40, 64) or any(c not in "0123456789abcdef" for c in oid):
        raise GitError("Invalid commit OID; inspect the repository again.")
    return _run(repo, "show", "-s", "--format=%cI", oid, "--")


def fetch_prune(repo: Path) -> bool:
    """Run git fetch -p using Git's configured default remote selection.

    No remote is required for local-only repositories. Errors stop reporting
    rather than presenting cached data as freshly fetched.
    """
    if not _run(repo, "remote"):
        return False
    _run(repo, "fetch", "-p")
    return True
