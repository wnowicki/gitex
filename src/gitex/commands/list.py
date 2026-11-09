"""Read-only branch reporting, separate from terminal presentation."""

from dataclasses import dataclass
from pathlib import Path

from gitex.branches import BranchPolicy, branch_policy
from gitex.git import Relationship, RelationshipAssessment, compare_commits, snapshot
from gitex.models import RefState, RepoSnapshot


@dataclass(frozen=True)
class BranchReport:
    ref: RefState
    name: str
    local: bool
    current: bool
    policy: BranchPolicy
    source_ref: str | None
    relationship: RelationshipAssessment | None
    readiness: str
    worktrees: tuple[Path, ...]


@dataclass(frozen=True)
class ListReport:
    repository: RepoSnapshot
    branches: tuple[BranchReport, ...]


def list_branches(repo: Path) -> ListReport:
    """Report local and cached remote branches; never fetch or probe by merging."""
    state = snapshot(repo)
    refs = {ref.name: ref for ref in state.refs}
    reports = []
    for ref in state.refs:
        if ref.symbolic_target:
            continue  # Remote HEAD is an alias, not a separate branch.
        local = ref.name.startswith("refs/heads/")
        relative = ref.name.removeprefix("refs/heads/" if local else "refs/remotes/")
        remote = None
        name = relative
        if not local:
            # Remote names may themselves contain slashes; prefer longest match.
            remote = next(
                (
                    r
                    for r in sorted(state.remotes, key=len, reverse=True)
                    if relative.startswith(r + "/")
                ),
                None,
            )
            name = relative[len(remote) + 1 :] if remote else relative
        policy = branch_policy(name, state.config.branches)
        source_ref = None
        relation = None
        readiness = "no integration policy"
        if policy.integration_source:
            source_ref = (
                f"refs/heads/{policy.integration_source}"
                if local
                else f"refs/remotes/{remote}/{policy.integration_source}"
                if remote
                else None
            )
            source = refs.get(source_ref)
            readiness = "source missing"
            if source:
                relation = compare_commits(state.root, ref.oid, source.oid)
                readiness = {
                    Relationship.IDENTICAL: "source included",
                    Relationship.AHEAD: "source included",
                    Relationship.BEHIND: "fast-forward possible",
                    Relationship.DIVERGED: f"{policy.strategy.value} needed; conflicts possible",
                }[relation.relationship]
        reports.append(
            BranchReport(
                ref,
                name,
                local,
                ref.name == state.head_ref,
                policy,
                source_ref,
                relation,
                readiness,
                tuple(w.path for w in state.worktrees if w.branch == ref.name),
            )
        )
    return ListReport(state, tuple(reports))
