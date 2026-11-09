"""Literal-text Rich rendering of branch inspection reports."""

from rich.console import Console
from rich.table import Table
from rich.text import Text

from gitex.branches import BranchType
from gitex.commands.list import BranchReport, ListReport


def render_list(report: ListReport, console: Console) -> None:
    """Render grouped status without interpreting branch names as markup."""
    state = report.repository
    console.print(Text(f"Repository: {state.root}"))
    head = state.head_ref or f"detached at {state.head_oid}"
    if state.head_oid is None:
        head = f"{state.head_ref} (unborn)"
    console.print(Text(f"HEAD: {head}; working tree: {'dirty' if state.dirty else 'clean'}"))
    if state.operations:
        console.print(Text("Operation in progress: " + ", ".join(state.operations), style="yellow"))
    console.print("Remote freshness unknown; cached refs may be stale. No fetch performed.")
    console.print("Conflict assessment is advisory; merges/rebases were not attempted.")
    if not report.branches:
        console.print("No branches found.")
    for kind in BranchType:
        branches = [b for b in report.branches if b.policy.kind == kind]
        if not branches:
            continue
        table = Table(title=kind.value, box=None, expand=False)
        for title in (
            "Branch",
            "Upstream / tracking",
            "Workflow source / relationship",
            "Readiness",
            "Worktrees",
        ):
            table.add_column(title, overflow="fold")
        for branch in sorted(branches, key=lambda b: (not b.local, b.ref.name)):
            ref = branch.ref
            tracking = _tracking(branch)
            source = branch.source_ref or "—"
            if branch.relationship:
                relation = branch.relationship
                source += f": {relation.relationship.value} (+{relation.ahead}/-{relation.behind})"
            style = (
                "yellow"
                if "possible" in branch.readiness or "missing" in branch.readiness
                else "green"
                if branch.relationship
                else ""
            )
            table.add_row(
                Text(("* " if branch.current else "") + ref.name.removeprefix("refs/")),
                Text(tracking),
                Text(source),
                Text(branch.readiness, style=style),
                Text(", ".join(str(p) for p in branch.worktrees) or "—"),
            )
        console.print(table)


def _tracking(branch: BranchReport) -> str:
    ref = branch.ref
    if not ref.upstream:
        return "remote-only" if not branch.local else "no upstream"
    if ref.upstream_oid is None:
        return f"{ref.upstream}: missing"
    tracking = f"{ref.upstream}: ahead {ref.ahead}, behind {ref.behind}"
    if ref.ahead and ref.behind:
        tracking += " (diverged)"
    return tracking
