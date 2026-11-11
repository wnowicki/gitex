"""Immutable inspection and operation records, independent of presentation."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gitex.config import Config


@dataclass(frozen=True)
class FileStatus:
    path: str
    index: str
    worktree: str
    original_path: str | None = None


@dataclass(frozen=True)
class RefState:
    name: str
    oid: str
    symbolic_target: str | None = None
    upstream: str | None = None
    upstream_oid: str | None = None
    remote: str | None = None
    remote_ref: str | None = None
    ahead: int | None = None
    behind: int | None = None


@dataclass(frozen=True)
class WorktreeState:
    path: Path
    head_oid: str | None
    branch: str | None
    bare: bool = False
    locked: bool = False
    prunable: bool = False


class RemoteFreshness(StrEnum):
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RepoSnapshot:
    root: Path
    head_oid: str | None
    head_ref: str | None
    files: tuple[FileStatus, ...]
    operations: tuple[str, ...]
    refs: tuple[RefState, ...]
    worktrees: tuple[WorktreeState, ...]
    remotes: tuple[str, ...]
    config: Config
    remote_freshness: RemoteFreshness = RemoteFreshness.UNKNOWN

    @property
    def dirty(self) -> bool:
        return bool(self.files)


class Action(StrEnum):
    FETCH = "fetch"
    FAST_FORWARD = "fast-forward"
    REBASE = "rebase"
    MERGE = "merge"
    PUSH = "push"


@dataclass(frozen=True)
class RefPrecondition:
    ref: str
    expected_oid: str | None


@dataclass(frozen=True)
class PlanStep:
    action: Action
    target: str
    source: str | None = None
    preconditions: tuple[RefPrecondition, ...] = ()
    local_changes: tuple[str, ...] = ()
    remote_changes: tuple[str, ...] = ()
    depends_on: tuple[int, ...] = ()


@dataclass(frozen=True)
class OperationPlan:
    steps: tuple[PlanStep, ...]
    warnings: tuple[str, ...] = ()


class Outcome(StrEnum):
    PLANNED = "planned"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class OperationResult:
    outcome: Outcome
    reason: str
    recovery: str | None = None
    local_applied: bool = False
    published: bool = False
