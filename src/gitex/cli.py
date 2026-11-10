"""Thin command-line entry points."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from gitex.commands.list import list_branches
from gitex.errors import ConfigError, GitError
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
    """Show branches, parent divergence, merge readiness, and last commit date."""
    try:
        report = list_branches(path)
    except (GitError, ConfigError, OSError) as exc:
        Console(stderr=True).print(f"Error: {exc}", markup=False)
        raise typer.Exit(1) from exc
    render_list(report, Console())
