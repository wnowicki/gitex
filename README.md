# GitEXtra

A compact Python CLI for safe GitFlow maintenance. The first implementation
step provides package scaffolding and a read-only Git relationship adapter.

Requires Python 3.12+ and Git. Install from this checkout with
[uv](https://docs.astral.sh/uv/guides/tools/):

```sh
uv tool install .
gitex --help
```

For development:

```sh
uv sync --dev
uv run pytest
uv run gitex --help
uv run ruff check src tests
uv build
```

Use `uv tool install --editable .` for an editable global installation.
The package lives in `src/gitex`; Git subprocess calls belong in `git.py`.
Relationship assessment compares pinned commit OIDs and reports identical,
ahead, behind, or diverged histories. It never fetches or writes Git objects,
and divergence does not imply a merge conflict. Remote-tracking information
may be stale.

---
Copyright (c) 2026 wnowicki.dev
