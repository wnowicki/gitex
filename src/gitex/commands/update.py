"""Compose upstream sync, source integration and safe publication."""

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from gitex.branches import BranchPolicy, BranchType, branch_policy
from gitex.commands.integrate import integrate_branch
from gitex.commands.sync import sync_branch
from gitex.errors import ConfigError, GitError
from gitex.git import create_update_worktree, remote_endpoint, remove_update_worktree, snapshot
from gitex.models import (
    Action,
    BranchResult,
    OperationPlan,
    OperationResult,
    Outcome,
    PlanStep,
    RefPrecondition,
    RefState,
    RepoSnapshot,
)

_FAILED = (Outcome.BLOCKED, Outcome.FAILED, Outcome.SKIPPED)


def _identity(state: RepoSnapshot, ref: RefState) -> tuple[str, ...]:
    if not ref.remote or ref.remote not in state.remotes or not ref.upstream or not ref.remote_ref:
        raise GitError("Upstream missing; configure an explicit remote-tracking upstream.")
    return (
        ref.name,
        ref.upstream,
        ref.remote,
        ref.remote_ref,
        remote_endpoint(state.root, ref.remote),
    )


def _combine(result: OperationResult, applied: bool, published: bool) -> OperationResult:
    applied = applied or result.local_applied
    published = published or result.published
    reason = result.reason
    if applied and result.outcome in _FAILED:
        reason = "Local changes applied; later update stage blocked: " + reason
    return replace(result, reason=reason, local_applied=applied, published=published)


def update_branch(
    repo: Path,
    name: str | None = None,
    *,
    push: bool | None = None,
    dry_run: bool = False,
    show_plan: Callable[[OperationPlan], None] = lambda plan: None,
) -> OperationResult:
    """Update the current checkout; retain local completion after later failures."""
    applied = published = False
    try:
        state = snapshot(repo)
        ref = next(
            (r for r in state.refs if r.name == (f"refs/heads/{name}" if name else state.head_ref)),
            None,
        )
        if not ref or ref.name != state.head_ref:
            raise GitError("Checkout the requested existing local branch before updating.")
        if dry_run:
            return _preview(state, ref, push, show_plan)
        identity = _identity(state, ref)
        policy = branch_policy(ref.name.removeprefix("refs/heads/"), state.config.branches)
        publish = state.config.workflow.push_on_sync if push is None else push

        guarded_plan = partial(_guard_plan, state, ref, identity, policy, show_plan)

        # Do not publish an intermediate state before workflow integration.
        result = sync_branch(state.root, name, push=False, show_plan=guarded_plan)
        applied = result.local_applied
        if result.outcome in _FAILED:
            return _combine(result, applied, published)
        if policy.integration_source:
            result = integrate_branch(state.root, name, show_plan=guarded_plan)
            applied = applied or result.local_applied
            if result.outcome in _FAILED:
                return _combine(result, applied, published)
        if not publish:
            return OperationResult(
                Outcome.UPDATED if applied else Outcome.UNCHANGED,
                "Update completed locally; publication disabled.",
                local_applied=applied,
            )
        result = sync_branch(state.root, name, push=publish, show_plan=guarded_plan)
        result = _combine(result, applied, published)
        if result.outcome not in _FAILED:
            return replace(
                result,
                outcome=Outcome.UPDATED
                if result.local_applied or result.published
                else Outcome.UNCHANGED,
                reason="Update completed locally; pushed."
                if result.published
                else "Update completed locally; nothing published.",
            )
        return result
    except (GitError, ConfigError, OSError) as exc:
        return OperationResult(
            Outcome.FAILED if applied else Outcome.BLOCKED,
            ("Local changes applied; " if applied else "") + str(exc),
            "Inspect git status, upstream and configuration, then retry.",
            applied,
            published,
        )


def _preview(
    state: RepoSnapshot,
    ref: RefState,
    push: bool | None,
    show_plan: Callable[[OperationPlan], None],
) -> OperationResult:
    """Describe conditional stages from cached refs; never simulate by mutation."""
    if state.dirty or state.operations or ref.symbolic_target:
        raise GitError(
            "Dirty checkout, ongoing operation or symbolic branch; resolve before update."
        )
    _identity(state, ref)
    if not ref.upstream_oid or (ref.ahead and ref.behind):
        raise GitError("Missing upstream or divergent history; reconcile before updating.")
    policy = branch_policy(ref.name.removeprefix("refs/heads/"), state.config.branches)
    if policy.kind == BranchType.UNKNOWN:
        raise GitError("Unknown branch policy; update is unsupported.")
    conditions = (
        RefPrecondition(ref.name, ref.oid),
        RefPrecondition(ref.upstream, ref.upstream_oid),
    )
    steps = [
        PlanStep(
            Action.FETCH,
            ref.upstream,
            ref.remote_ref,
            conditions,
            (ref.upstream, "objects", "FETCH_HEAD"),
        )
    ]
    if ref.behind:
        steps.append(
            PlanStep(
                Action.FAST_FORWARD,
                ref.name,
                ref.upstream,
                conditions,
                (ref.name, "index", "working tree"),
            )
        )
    if policy.integration_source:
        parent = next(
            (r for r in state.refs if r.name == f"refs/heads/{policy.integration_source}"), None
        )
        if not parent or parent.symbolic_target or not parent.upstream_oid:
            raise GitError(
                "Workflow source or source upstream missing; configure it before updating."
            )
        _identity(state, parent)
        steps.append(
            PlanStep(
                Action.FETCH,
                parent.upstream,
                parent.remote_ref,
                (
                    RefPrecondition(parent.name, parent.oid),
                    RefPrecondition(parent.upstream, parent.upstream_oid),
                ),
                (parent.upstream, "objects", "FETCH_HEAD"),
            )
        )
        steps.append(
            PlanStep(
                Action.REBASE
                if policy.kind in (BranchType.FEATURE, BranchType.BUGFIX)
                else Action.MERGE,
                ref.name,
                parent.name,
                conditions + (RefPrecondition(parent.name, parent.oid),),
                (ref.name, "index", "working tree"),
            )
        )
    if state.config.workflow.push_on_sync if push is None else push:
        steps.append(
            PlanStep(
                Action.FETCH,
                ref.upstream,
                ref.remote_ref,
                conditions,
                (ref.upstream, "objects", "FETCH_HEAD"),
            )
        )
        steps.append(
            PlanStep(
                Action.PUSH,
                ref.remote_ref,
                ref.name,
                conditions,
                remote_changes=(f"{ref.remote}:{ref.remote_ref}",),
            )
        )
    show_plan(
        OperationPlan(
            tuple(steps),
            (
                "Conditional plan only: cached remote knowledge may be stale; no fetch, checkout or mutation.",
                "Each stage is revalidated after previous changes; divergence or base publication may block later stages.",
            ),
        )
    )
    return OperationResult(Outcome.PLANNED, "Conditional update planned; no state changed.")


def dependency_order(state: RepoSnapshot) -> tuple[tuple[str, ...], dict[str, str | None]]:
    """Order eligible local branches by configured integration dependencies."""
    policies = {
        r.name.removeprefix("refs/heads/"): branch_policy(
            r.name.removeprefix("refs/heads/"), state.config.branches
        )
        for r in state.refs
        if r.name.startswith("refs/heads/")
    }
    dependencies = {
        name: policy.integration_source
        for name, policy in policies.items()
        if policy.kind != BranchType.UNKNOWN
    }
    ordered: list[str] = []
    pending = set(dependencies)
    while pending:
        ready = sorted(name for name in pending if dependencies[name] not in pending)
        if not ready:
            raise GitError("Branch dependency cycle; fix branch configuration.")
        ordered.extend(ready)
        pending.difference_update(ready)
    return tuple(ordered), dependencies


def update_all(
    repo: Path,
    *,
    push: bool | None = None,
    dry_run: bool = False,
    show_plan: Callable[[OperationPlan], None] = lambda plan: None,
) -> tuple[BranchResult, ...]:
    """Continue independent branches, preserving failed worktrees for recovery."""
    state = snapshot(repo)
    if state.dirty or state.operations:
        raise GitError(
            "Clean the current checkout and finish ongoing operations before update --all."
        )
    order, dependencies = dependency_order(state)
    results: dict[str, BranchResult] = {}
    for name in order:
        dependency = dependencies[name]
        if dependency in results and results[dependency].result.outcome in _FAILED:
            results[name] = BranchResult(
                name, OperationResult(Outcome.SKIPPED, f"Dependency {dependency} did not complete.")
            )
            continue
        results[name] = _update_one(state, name, push, dry_run, show_plan)
    return tuple(results.values())


def _update_one(
    state: RepoSnapshot,
    name: str,
    push: bool | None,
    dry_run: bool,
    show_plan: Callable[[OperationPlan], None],
) -> BranchResult:
    path = None
    result = OperationResult(Outcome.BLOCKED, "Update not started.")
    try:
        current = snapshot(state.root)
        ref = _scheduled_ref(state, current, name)
        if any(
            w.branch == ref.name and w.path.resolve() != state.root.resolve()
            for w in current.worktrees
        ):
            raise GitError("Checked out in another worktree; branch was not modified.")
        if dry_run:
            result = _preview(current, ref, push, show_plan)
        elif current.head_ref == ref.name:
            result = update_branch(state.root, name, push=push, show_plan=show_plan)
        else:
            show_plan(
                OperationPlan(
                    (
                        PlanStep(
                            Action.WORKTREE_ADD,
                            ref.name,
                            preconditions=(RefPrecondition(ref.name, ref.oid),),
                            local_changes=("worktree metadata", "isolated HEAD/index/files"),
                        ),
                    ),
                    (f"Create isolated worktree for {name}; remove only if clean and complete.",),
                )
            )
            path = create_update_worktree(state.root, name, ref.oid)
            if snapshot(path).config != state.config:
                raise GitError(
                    "Branch configuration differs from the dependency plan; inspect the preserved worktree."
                )
            result = update_branch(path, name, push=push, show_plan=show_plan)
            if result.outcome not in _FAILED:
                show_plan(
                    OperationPlan(
                        (
                            PlanStep(
                                Action.WORKTREE_REMOVE,
                                str(path),
                                local_changes=("isolated worktree and metadata",),
                            ),
                        )
                    )
                )
                remove_update_worktree(state.root, path)
                path = None
    except (GitError, ConfigError, OSError) as exc:
        result = _combine(
            OperationResult(Outcome.BLOCKED, str(exc)), result.local_applied, result.published
        )
    return BranchResult(name, result, path)


def _scheduled_ref(initial: RepoSnapshot, current: RepoSnapshot, name: str) -> RefState:
    ref = next((r for r in current.refs if r.name == f"refs/heads/{name}"), None)
    if ref is None or ref.symbolic_target:
        raise GitError("Scheduled branch is missing or symbolic; inspect and retry.")
    if current.config != initial.config:
        raise GitError(
            "Repository configuration changed after dependency planning; inspect and retry."
        )
    _identity(current, ref)
    if not ref.upstream_oid:
        raise GitError(
            "Upstream missing; configure and fetch an explicit upstream before updating."
        )
    return ref


def _guard_plan(
    state: RepoSnapshot,
    ref: RefState,
    identity: tuple[str, ...],
    policy: BranchPolicy,
    show_plan: Callable[[OperationPlan], None],
    plan: OperationPlan,
) -> None:
    show_plan(plan)
    current_state = snapshot(state.root)
    current = next((r for r in current_state.refs if r.name == ref.name), None)
    if (
        not current
        or current_state.head_ref != ref.name
        or _identity(current_state, current) != identity
        or branch_policy(ref.name.removeprefix("refs/heads/"), current_state.config.branches)
        != policy
    ):
        raise GitError("Update branch, upstream, endpoint or policy changed; inspect and retry.")
