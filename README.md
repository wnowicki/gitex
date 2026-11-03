# GitEX

The script supports Git flow maintenance for the current repository or a repository supplied by path.

It fetches branches from origin and checks whether develop includes the latest main changes. If develop can be fast-forwarded, it updates and pushes it. Otherwise, it reports whether a merge would be clean or cause conflicts.

It then checks other remote branches against develop, skipping main, develop, release/*, and hotfix/* branches. Release/* and hotfix/* branches are checked separately against main.

By default, only develop is updated. Parameters allow selecting other branches, changing branch names or the remote, and running a dry run without pushing. Local branches and working files remain unchanged.

Install globally from this checkout with [uv](https://docs.astral.sh/uv/guides/tools/) (Python 3.9+ and Git are required):

```sh
uv tool install .
```

Run from any directory:

```sh
gitex --help
gitex --dry-run
gitex /path/to/repository --dry-run
```

For development, use `uv tool install --editable .` so edits to `gitex.py` apply immediately. To reinstall a regular installation after changing the source, run `uv tool install --reinstall .` from this checkout. If `gitex` is not on your PATH, run `uv tool update-shell` and restart your shell.

---
Copyright (c) 2026 wnowicki.dev
