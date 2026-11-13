"""Safe synchronization of the current checkout against its explicit upstream."""

from collections.abc import Callable
from pathlib import Path

from gitex.branches import BranchType, branch_policy
from gitex.config import valid_ref_name
from gitex.errors import GitError
from gitex.git import fast_forward, fetch_upstream, publish_upstream, remote_endpoint, snapshot
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


def _target(state: RepoSnapshot, name: str | None) -> RefState:
    refname = f"refs/heads/{name}" if name else state.head_ref
    ref = next((r for r in state.refs if r.name == refname), None)
    if ref is None:
        raise GitError("Local branch missing or HEAD detached/unborn; checkout an existing branch.")
    if ref.name != state.head_ref:
        raise GitError(
            "Only the current checkout is supported; switch to the branch in its owning worktree."
        )
    if state.dirty or state.operations:
        raise GitError(
            "Working tree is dirty or an operation is ongoing; commit your work or finish the operation first."
        )
    if any(
        w.branch == ref.name and w.path.resolve() != state.root.resolve() for w in state.worktrees
    ):
        raise GitError("Branch is checked out in another worktree; use that worktree.")
    if (
        branch_policy(ref.name.removeprefix("refs/heads/"), state.config.branches).kind
        == BranchType.UNKNOWN
    ):
        raise GitError("Unknown branch policy; configure a supported branch before syncing.")
    if not ref.upstream or not ref.upstream_oid:
        raise GitError("Upstream missing; configure and fetch an explicit upstream before syncing.")
    upstream = next(r for r in state.refs if r.name == ref.upstream)
    if upstream.symbolic_target:
        raise GitError("Symbolic upstream is unsupported; configure a direct remote-tracking ref.")
    if (
        not ref.remote
        or ref.remote not in state.remotes
        or not valid_ref_name(ref.remote)
        or not ref.upstream.startswith("refs/remotes/")
        or not ref.remote_ref
        or not ref.remote_ref.startswith("refs/heads/")
    ):
        raise GitError(
            "Unsupported upstream; configure a named remote with a remote-tracking branch."
        )
    return ref


def _plan(state: RepoSnapshot, ref: RefState, push: bool) -> OperationPlan:
    conditions = (
        RefPrecondition(ref.name, ref.oid),
        RefPrecondition(ref.upstream, ref.upstream_oid),
    )
    if ref.ahead and ref.behind:
        raise GitError(
            "Upstream diverged; reconcile history manually, then retry. No force-push is performed."
        )
    steps = []
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
    if ref.ahead and push:
        bases = (state.config.branches.main, state.config.branches.develop)
        if (
            ref.name.removeprefix("refs/heads/") in bases
            or ref.remote_ref.removeprefix("refs/heads/") in bases
        ):
            raise GitError("Publication to a base branch is blocked; use a PR/MR or --no-push.")
        steps.append(
            PlanStep(
                Action.PUSH,
                ref.remote_ref,
                ref.name,
                conditions,
                remote_changes=(f"{ref.remote}:{ref.remote_ref}",),
            )
        )
    return OperationPlan(tuple(steps))


def sync_branch(
    repo: Path,
    name: str | None = None,
    *,
    push: bool | None = None,
    dry_run: bool = False,
    show_plan: Callable[[OperationPlan], None] = lambda plan: None,
) -> OperationResult:
    """Validate, plan, fetch, replan, and apply; never reconcile divergence."""
    applied = published = False
    try:
        state = snapshot(repo)
        ref = _target(state, name)
        publish = state.config.workflow.push_on_sync if push is None else push
        if dry_run:
            plan = _plan(state, ref, publish)
            show_plan(
                OperationPlan(plan.steps, ("Remote knowledge may be stale; no fetch or mutation.",))
            )
            return OperationResult(
                Outcome.PLANNED, "Cached plan only; execution revalidates after fetch."
            )
        endpoint = remote_endpoint(state.root, ref.remote)
        show_plan(
            OperationPlan(
                (
                    PlanStep(
                        Action.FETCH,
                        ref.upstream,
                        ref.remote_ref,
                        (RefPrecondition(ref.name, ref.oid),),
                        (ref.upstream, "objects", "FETCH_HEAD"),
                    ),
                )
            )
        )
        original_oid = ref.oid
        if _target(snapshot(state.root), name) != ref:
            raise GitError("Branch state changed before fetch; inspect and retry.")
        _check_endpoint(state.root, ref.remote, endpoint)
        fetch_upstream(state.root, endpoint, ref.remote_ref, ref.upstream)
        _check_endpoint(state.root, ref.remote, endpoint)
        state = snapshot(state.root)
        fresh = _target(state, name)
        if (
            fresh.name != ref.name
            or fresh.oid != original_oid
            or (fresh.upstream, fresh.remote, fresh.remote_ref)
            != (ref.upstream, ref.remote, ref.remote_ref)
        ):
            raise GitError("Branch or upstream changed during fetch; inspect and retry.")
        plan = _plan(state, fresh, publish)
        show_plan(plan)
        for step in plan.steps:
            current = _target(snapshot(state.root), name)
            if current != fresh:
                raise GitError("Branch state changed after planning; inspect and retry.")
            _check_endpoint(state.root, fresh.remote, endpoint)
            if step.action == Action.FAST_FORWARD:
                fast_forward(state.root, fresh.upstream_oid)
                applied = True
            elif step.action == Action.PUSH:
                publish_upstream(state.root, endpoint, fresh.oid, fresh.remote_ref)
                published = True
        return OperationResult(
            Outcome.UPDATED if applied or published else Outcome.UNCHANGED,
            "Pushed."
            if published
            else "Locally fast-forwarded."
            if applied
            else "No local changes; nothing published.",
            local_applied=applied,
            published=published,
        )
    except GitError as exc:
        return OperationResult(
            Outcome.BLOCKED,
            str(exc),
            "Inspect git status and upstream refs, then retry.",
            applied,
            published,
        )


def _check_endpoint(repo: Path, remote: str, expected: str) -> None:
    if remote_endpoint(repo, remote) != expected:
        raise GitError(
            "Remote endpoint changed; inspect remote configuration and retry. Nothing published."
        )
