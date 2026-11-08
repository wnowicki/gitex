"""Command-line presentation; workflow commands arrive in later steps."""

import typer

app = typer.Typer(help="GitEXtra — safe GitFlow maintenance.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Inspect and maintain GitFlow branches (core scaffolding)."""
