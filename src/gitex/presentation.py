"""Compact literal-text branch table."""

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from gitex.commands.list import ListReport


def render_list(report: ListReport, console: Console) -> None:
    """Show one table; local tips take precedence when both copies exist."""
    state = report.repository
    table = Table(box=box.SIMPLE, padding=(0, 1), expand=False)
    for title in ("Branch", "Where", "Parent", "− | +", "Merge", "Last commit"):
        table.add_column(title, overflow="fold")
    for branch in report.branches:
        location = (
            "both" if branch.local and branch.remote_refs else "local" if branch.local else "remote"
        )
        parent = branch.source_ref or "—"
        parent = parent.removeprefix("refs/heads/").removeprefix("refs/remotes/")
        counts = Text("—", style="dim")
        if branch.relationship:
            relation = branch.relationship
            counts = Text()
            counts.append(str(relation.behind), style="yellow" if relation.behind else "dim")
            counts.append(" | ", style="dim")
            counts.append(str(relation.ahead), style="green" if relation.ahead else "dim")
        readiness = branch.readiness
        style = (
            "yellow" if "possible" in readiness else "red" if "missing" in readiness else "green"
        )
        name = branch.name if branch.local else branch.ref.name.removeprefix("refs/remotes/")
        table.add_row(
            Text(
                ("* " if branch.current else "  ") + name,
                style="bold cyan" if branch.current else "",
            ),
            Text(
                location + (" ≠" if branch.remote_differs else ""),
                style={"both": "cyan", "local": "blue", "remote": "magenta"}[location],
            ),
            Text(parent, style="magenta" if branch.parent_inferred else ""),
            counts,
            Text(readiness.replace("ready (fast-forward)", "ready"), style=style),
            Text(branch.last_commit[:10], style="dim"),
        )
    console.print(table if report.branches else Text("No branches found."))
    notes = ["* current", "≠ local/remote tips differ", "dates: committer"]
    if any(branch.parent_inferred for branch in report.branches):
        notes.append("magenta parent: assumed develop")
    if state.dirty:
        notes.append("dirty worktree")
    if state.head_ref is None:
        notes.append("detached HEAD")
    elif state.head_oid is None:
        notes.append("unborn HEAD")
    if state.operations:
        notes.append("in progress: " + ", ".join(state.operations))
    console.print(Text(" · ".join(notes), style="dim"))
    console.print(
        Text(
            (
                "Fetched with prune; other remotes may be stale. "
                if report.fetched
                else "Cached refs may be stale. "
            )
            + "Conflicts possible is advisory. Both uses the local tip.",
            style="dim",
        )
    )
