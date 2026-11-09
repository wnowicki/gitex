# GitEXtra

A compact Python CLI for safe GitFlow maintenance. The first implementation
steps provide package scaffolding, configuration, branch policies, and read-only
Git relationship and repository snapshot adapters. `list` reports branch status;
state-changing workflow commands are not available yet.

Requires Python 3.12+ and Git. Install from this checkout with
[uv](https://docs.astral.sh/uv/guides/tools/):

```sh
uv tool install .
gitex --help
gitex list
gitex list /path/to/repository
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

Optional repository configuration uses `.gitex.toml` with the documented
`branches`, `workflow`, and `release` tables.
Explicit overrides take precedence over configuration, then built-in defaults.
Unknown keys, invalid types, overlapping branch policies, and invalid ref names
are rejected. Release editing supports only `CHANGELOG.md` in v1.

Snapshots capture HEAD (including unborn/detached states), file/index status,
ongoing Git operations, local/remote refs, upstream divergence, and linked
worktrees. Remote freshness remains unknown: inspection never fetches.
Snapshots are observations; later execution must revalidate critical OIDs.

---
Licensed under the [MIT License](LICENSE).

Copyright (c) 2026 wnowicki.dev
