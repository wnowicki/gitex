"""Thin command-line entry points."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from gitex.commands.list import list_branches
from gitex.commands.sync import sync_branch
from gitex.errors import ConfigError, GitError
from gitex.models import OperationPlan, Outcome
from gitex.presentation import render_list

app = typer.Typer(help="GitEXtra — safe GitFlow maintenance.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Inspect and maintain GitFlow branches."""


@app.command("list")
def list_command(
    path: Annotated[
        Path, typer.Argument(help="Repository path (defaults to current directory).")
    ] = Path("."),
) -> None:
    """Fetch/prune, then show branches ordered by newest commit."""
    try:
        report = list_branches(path)
    except (GitError, ConfigError, OSError) as exc:
        Console(stderr=True).print(f"Error: {exc}", markup=False)
        raise typer.Exit(1) from exc
    render_list(report, Console())


def _show_sync_plan(plan: OperationPlan) -> None:
    console = Console()
    for warning in plan.warnings:
        console.print(warning, markup=False)
    for step in plan.steps:
        console.print(
            f"Plan: {step.action.value} {step.source or ''} -> {step.target}", markup=False
        )
        if step.local_changes:
            console.print("  Local: " + ", ".join(step.local_changes), markup=False)
        if step.remote_changes:
            console.print("  Remote: " + ", ".join(step.remote_changes), markup=False)


@app.command("sync")
def sync_command(
    name: Annotated[
        str | None, typer.Argument(help="Local branch (default: current checkout).")
    ] = None,
    path: Annotated[Path, typer.Option("--path", help="Repository path.")] = Path("."),
    push: Annotated[bool | None, typer.Option("--push/--no-push")] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Read-only cached plan; never fetch.")
    ] = False,
) -> None:
    """Fetch tracked upstream, fast-forward or safely publish the current checkout."""
    try:
        result = sync_branch(path, name, push=push, dry_run=dry_run, show_plan=_show_sync_plan)
    except (ConfigError, OSError) as exc:
        Console(stderr=True).print(f"Error: {exc}", markup=False)
        raise typer.Exit(1) from exc
    Console().print(f"{result.outcome.value}: {result.reason}", markup=False)
    if result.recovery:
        Console().print(result.recovery, markup=False)
    if result.outcome in (Outcome.BLOCKED, Outcome.FAILED):
        raise typer.Exit(1)
