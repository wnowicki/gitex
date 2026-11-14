"""Incorporate a validated workflow source without publishing."""

from collections.abc import Callable
from pathlib import Path

from gitex.branches import BranchPolicy, Strategy, branch_policy
from gitex.config import valid_ref_name
from gitex.errors import GitError
from gitex.git import (
    Relationship,
    compare_commits,
    fetch_upstream,
    integrate_commit,
    remote_endpoint,
    snapshot,
)
from gitex.models import (
    Action,
    OperationPlan,
    OperationResult,
    Outcome,
    PlanStep,
    RefPrecondition,
    RefState,
    RepoSnapshot,
)


def _inspect(
    repo: Path, name: str | None, source: str | None
) -> tuple[RepoSnapshot, RefState, RefState, BranchPolicy]:
    state = snapshot(repo)
    target_name = f"refs/heads/{name}" if name else state.head_ref
    target = next((r for r in state.refs if r.name == target_name), None)
    if not target or target.symbolic_target or target.name != state.head_ref:
        raise GitError("Checkout the requested direct local branch before integrating.")
    if state.dirty or state.operations:
        raise GitError(
            "Dirty worktree/index or ongoing operation; commit work or finish the operation first."
        )
    if any(
        w.branch == target.name and w.path.resolve() != state.root.resolve()
        for w in state.worktrees
    ):
        raise GitError("Branch is checked out in another worktree; use that worktree.")
    policy = branch_policy(target.name.removeprefix("refs/heads/"), state.config.branches)
    if not policy.strategy or not policy.integration_source:
        raise GitError("No supported integration policy for this branch.")
    source_name = source if source is not None else policy.integration_source
    if not valid_ref_name(source_name):
        raise GitError("Invalid source branch name.")
    parent = next((r for r in state.refs if r.name == f"refs/heads/{source_name}"), None)
    if not parent or parent.symbolic_target or parent.name == target.name:
        raise GitError("Source must be a different existing direct local branch.")
    if (
        not parent.upstream_oid
        or not parent.upstream
        or not parent.remote
        or parent.remote not in state.remotes
        or not valid_ref_name(parent.remote)
        or not parent.remote_ref
        or not parent.remote_ref.startswith("refs/heads/")
        or not parent.upstream.startswith("refs/remotes/")
    ):
        raise GitError(
            "Source needs an existing tracked upstream; configure it and sync the source first."
        )
    if next(r for r in state.refs if r.name == parent.upstream).symbolic_target:
        raise GitError("Symbolic source upstream is unsupported.")
    return state, target, parent, policy


def integrate_branch(
    repo: Path,
    name: str | None = None,
    *,
    source: str | None = None,
    dry_run: bool = False,
    show_plan: Callable[[OperationPlan], None] = lambda plan: None,
) -> OperationResult:
    """Fetch source knowledge, require exact source/upstream equality, then integrate."""
    strategy = None
    try:
        state, target, parent, policy = _inspect(repo, name, source)
        strategy = policy.strategy
        endpoint = remote_endpoint(state.root, parent.remote)
        if not dry_run:
            show_plan(
                OperationPlan(
                    (
                        PlanStep(
                            Action.FETCH,
                            parent.upstream,
                            parent.remote_ref,
                            (
                                RefPrecondition(target.name, target.oid),
                                RefPrecondition(parent.name, parent.oid),
                            ),
                            (parent.upstream, "objects", "FETCH_HEAD"),
                        ),
                    )
                )
            )
            _revalidate(state.root, name, source, target, parent, endpoint, policy)
            fetch_upstream(state.root, endpoint, parent.remote_ref, parent.upstream)
            new_state, new_target, new_parent, new_policy = _inspect(state.root, name, source)
            if (
                new_target.name != target.name
                or new_target.oid != target.oid
                or new_target.upstream != target.upstream
                or new_target.remote != target.remote
                or new_target.remote_ref != target.remote_ref
                or (
                    target.upstream != parent.upstream
                    and new_target.upstream_oid != target.upstream_oid
                )
                or new_parent.oid != parent.oid
                or (new_parent.name, new_parent.upstream, new_parent.remote, new_parent.remote_ref)
                != (parent.name, parent.upstream, parent.remote, parent.remote_ref)
                or new_policy != policy
                or remote_endpoint(state.root, parent.remote) != endpoint
            ):
                raise GitError(
                    "Branch, policy or endpoint changed during fetch; inspect and retry."
                )
            state, target, parent = new_state, new_target, new_parent
        if parent.oid != parent.upstream_oid:
            raise GitError(
                "Source is not equal to its upstream; sync/reconcile the source first, then retry."
            )
        relation = compare_commits(state.root, target.oid, parent.oid)
        if relation.relationship in (Relationship.IDENTICAL, Relationship.AHEAD):
            return OperationResult(
                Outcome.PLANNED if dry_run else Outcome.UNCHANGED, "Source already included."
            )
        warnings = (
            ("Cached remote knowledge may be stale; no fetch or mutation.",) if dry_run else ()
        )
        action = Action.REBASE if strategy == Strategy.REBASE else Action.MERGE
        show_plan(
            OperationPlan(
                (
                    PlanStep(
                        action,
                        target.name,
                        parent.name,
                        (
                            RefPrecondition(target.name, target.oid),
                            RefPrecondition(parent.name, parent.oid),
                            RefPrecondition(parent.upstream, parent.upstream_oid),
                        ),
                        (target.name, "index", "working tree"),
                    ),
                ),
                warnings,
            )
        )
        if dry_run:
            return OperationResult(Outcome.PLANNED, "Integration planned using cached refs only.")
        _revalidate(state.root, name, source, target, parent, endpoint, policy)
        integrate_commit(state.root, parent.oid, rebase=strategy == Strategy.REBASE)
        after = snapshot(state.root)
        updated = next(r for r in after.refs if r.name == target.name)
        rewritten = (
            strategy == Strategy.REBASE
            and target.upstream_oid is not None
            and compare_commits(state.root, updated.oid, target.upstream_oid).behind > 0
        )
        return OperationResult(
            Outcome.UPDATED,
            "Locally integrated; publication blocked by rewritten published history. Never force-push automatically."
            if rewritten
            else "Locally integrated; nothing published.",
            local_applied=True,
        )
    except GitError as exc:
        operation = "rebase" if strategy == Strategy.REBASE else "merge"
        return OperationResult(
            Outcome.BLOCKED,
            str(exc),
            f"Inspect git status. If {operation} stopped on conflicts, resolve and run git {operation} --continue, "
            f"or git {operation} --abort; then revalidate and retry.",
        )


def _revalidate(
    repo: Path,
    name: str | None,
    source: str | None,
    target: RefState,
    parent: RefState,
    endpoint: str,
    expected_policy: BranchPolicy,
) -> None:
    state, current, upstream, policy = _inspect(repo, name, source)
    if (
        current != target
        or policy != expected_policy
        or upstream != parent
        or remote_endpoint(state.root, parent.remote) != endpoint
    ):
        raise GitError("Branch or endpoint changed after planning; inspect and retry.")
