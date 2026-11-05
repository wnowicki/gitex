"""Fast-forward selected remote branches and report potential merge conflicts."""

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text


STATUS_STYLES = {
    "equal": "dim",
    "contains base": "green",
    "fast-forwarded": "green",
    "would fast-forward": "cyan",
    "fast-forward possible": "cyan",
    "CLEAN MERGE required": "yellow",
    "CONFLICT": "bold red",
}


def report(console, title, rows, plain=False):
    """Use tables interactively and unwrapped text for copying or piping."""
    console.print()
    console.print(Text(title, style="bold cyan"))
    if plain or not console.is_terminal:
        for name, status in rows:
            console.print(Text(f"  {name}: {status}"), soft_wrap=True)
        if not rows:
            console.print("  No branches to check.")
        return
    table = Table(box=box.SIMPLE, padding=(0, 1), collapse_padding=True)
    table.add_column("Branch", style="bold", overflow="fold")
    table.add_column("Status")
    for name, status in rows:
        table.add_row(Text(name), Text(status, style=STATUS_STYLES.get(status, "")))
    if rows:
        console.print(table)
    else:
        console.print("  No branches to check.", style="dim")


class GitError(RuntimeError):
    pass


def run(repo, *args, allowed=(0,)):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True
    )
    if result.returncode not in allowed:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result


def ancestor(repo, older, newer):
    return run(repo, "merge-base", "--is-ancestor", older, newer,
               allowed=(0, 1)).returncode == 0


def relationship(repo, branch, base):
    if branch == base:
        return "equal"
    if ancestor(repo, base, branch):
        return "contains base"
    if ancestor(repo, branch, base):
        return "fast-forward possible"
    # merge-tree evaluates a real three-way merge without changing the index,
    # working tree, or branch refs. Exit 1 means conflicts; other errors fail.
    result = run(repo, "merge-tree", "--write-tree", branch, base, allowed=(0, 1))
    return "CONFLICT" if result.returncode == 1 else "CLEAN MERGE required"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", nargs="?", default=".", type=Path)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--main", default="main", help="base branch (default: main)")
    parser.add_argument("--develop", default="develop")
    parser.add_argument(
        "--update", action="append", metavar="PATTERN",
        help="branch name or glob to fast-forward toward main; repeatable; default: develop",
    )
    parser.add_argument("--dry-run", action="store_true", help="fetch and report without pushing")
    parser.add_argument("--plain", action="store_true", help="use plain, unwrapped output for copying")
    args = parser.parse_args()
    console = Console(highlight=False, color_system=None if args.plain else "auto")
    repo = args.repository.resolve()
    remote = args.remote
    # Keep remote names safe both as command arguments and ref components.
    run(repo, "check-ref-format", f"refs/remotes/{remote}/placeholder")
    if remote.startswith("-"):
        raise GitError("Remote name cannot start with '-'.")
    run(repo, "rev-parse", "--git-dir")
    run(repo, "fetch", "--prune", "--no-tags", remote,
        f"+refs/heads/*:refs/remotes/{remote}/*")

    def branches():
        result = run(repo, "for-each-ref", "--format=%(refname) %(objectname) %(symref)",
                     f"refs/remotes/{remote}/")
        prefix = f"refs/remotes/{remote}/"
        found = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:  # Ignore symbolic refs such as origin/HEAD.
                found[parts[0][len(prefix):]] = parts[1]
        return found

    refs = branches()
    for required in (args.main, args.develop):
        if required not in refs:
            raise GitError(f"Required remote branch missing: {remote}/{required}")
    patterns = args.update if args.update is not None else [args.develop]
    selected = set()
    for pattern in patterns:
        matches = {name for name in refs if fnmatch.fnmatchcase(name, pattern)}
        if not matches:
            raise GitError(f"Update pattern matches no remote branches: {pattern}")
        selected.update(matches)
    selected.discard(args.main)
    skipped = False
    updates = []
    for name in sorted(selected):
        status = relationship(repo, refs[name], refs[args.main])
        if status == "fast-forward possible":
            if args.dry_run:
                status = "would fast-forward"
            else:
                # A normal push enforces fast-forward on the server too.
                run(repo, "push", remote, f"{refs[args.main]}:refs/heads/{name}")
                status = "fast-forwarded"
        else:
            skipped |= status in ("CONFLICT", "CLEAN MERGE required")
        updates.append((name, status))
    report(console, f"Fast-forward updates toward {remote}/{args.main}", updates, args.plain)

    # Read the actual server state after updates, including concurrent changes.
    if not args.dry_run:
        run(repo, "fetch", "--prune", "--no-tags", remote,
            f"+refs/heads/*:refs/remotes/{remote}/*")
        refs = branches()
        for required in (args.main, args.develop):
            if required not in refs:
                raise GitError(f"Remote branch disappeared: {remote}/{required}")
    conflicts = False
    checks = []
    for name in sorted(refs):
        if name in (args.main, args.develop) or name.startswith(("release/", "hotfix/")):
            continue
        status = relationship(repo, refs[name], refs[args.develop])
        checks.append((name, status))
        conflicts |= status == "CONFLICT"
    report(console, f"Branch checks against {remote}/{args.develop}"
           + (" (current state; dry-run updates not applied)" if args.dry_run else ""),
           checks, args.plain)
    checks = []
    for name in sorted(refs):
        if name.startswith(("release/", "hotfix/")):
            status = relationship(repo, refs[name], refs[args.main])
            checks.append((name, status))
            conflicts |= status == "CONFLICT"
    report(console, f"Release/hotfix checks against {remote}/{args.main}", checks, args.plain)
    return 1 if skipped or conflicts else 0


def cli():
    """Run the command with consistent error handling for all entry points."""
    try:
        return main()
    except (GitError, OSError) as error:
        Console(stderr=True, highlight=False).print(Text(f"Error: {error}", style="bold red"), soft_wrap=True)
        return 2


if __name__ == "__main__":
    sys.exit(cli())
