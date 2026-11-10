"""Read-only branch reporting, separate from terminal presentation."""

from dataclasses import dataclass
from pathlib import Path

from gitex.branches import BranchPolicy, branch_policy
from gitex.git import Relationship, RelationshipAssessment, commit_date, compare_commits, snapshot
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
    last_commit: str
    remote_refs: tuple[str, ...] = ()
    remote_differs: bool = False


@dataclass(frozen=True)
class ListReport:
    repository: RepoSnapshot
    branches: tuple[BranchReport, ...]


def list_branches(repo: Path) -> ListReport:
    """Report local and cached remote branches; never fetch or probe by merging."""
    state = snapshot(repo)
    refs = {ref.name: ref for ref in state.refs}
    reports = []
    locals_by_name = {
        r.name.removeprefix("refs/heads/"): r
        for r in state.refs
        if r.name.startswith("refs/heads/")
    }
    remote_groups: dict[str, list[RefState]] = {}
    for ref in state.refs:
        if not ref.name.startswith("refs/remotes/") or ref.symbolic_target:
            continue
        relative = ref.name.removeprefix("refs/remotes/")
        remote = next(
            (
                r
                for r in sorted(state.remotes, key=len, reverse=True)
                if relative.startswith(r + "/")
            ),
            None,
        )
        name = relative[len(remote) + 1 :] if remote else relative
        remote_groups.setdefault(name, []).append(ref)
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
        if not local and name in locals_by_name:
            continue
        policy = branch_policy(name, state.config.branches)
        parent = policy.delivery_target or policy.integration_source
        source_ref = None
        relation = None
        readiness = "—"
        if parent:
            source_ref = (
                f"refs/heads/{parent}"
                if local
                else f"refs/remotes/{remote}/{parent}"
                if remote
                else None
            )
            source = refs.get(source_ref)
            readiness = "parent missing"
            if source:
                relation = compare_commits(state.root, ref.oid, source.oid)
                readiness = {
                    Relationship.IDENTICAL: "merged",
                    Relationship.AHEAD: "ready (fast-forward)",
                    Relationship.BEHIND: "merged",
                    Relationship.DIVERGED: "conflicts possible",
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
                commit_date(state.root, ref.oid),
                tuple(r.name for r in remote_groups.get(name, ())) if local else (ref.name,),
                local and any(r.oid != ref.oid for r in remote_groups.get(name, ())),
            )
        )
    return ListReport(state, tuple(reports))
