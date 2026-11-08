"""Read-only Git inspection using argument arrays and bounded execution."""

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gitex.errors import GitError


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
    return result.stdout.strip()


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
