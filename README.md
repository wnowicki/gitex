# GitEXtra

A compact Python CLI for safe GitFlow maintenance. The first implementation
steps provide package scaffolding, configuration, branch policies, and read-only
Git relationship and repository snapshot adapters. `list` reports branch status;
`sync` safely synchronizes the current checkout with its upstream. Other
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

`list` shows one coloured table: branch, location (`local`, `remote`, `both`),
parent, behind/ahead (`− | +`), merge readiness, and last commit date (committer).
Matching local/remote names share a row. `*` marks the current branch; `≠` marks
unequal tips. Combined rows use the local tip for comparisons and dates.
Remote-only rows include the remote name.

Parent means the delivery target: feature/bugfix → develop, release/hotfix →
main. Develop compares with main; main has no parent. Unknown branches default to the configured develop branch
for reporting, shown in magenta; this does not grant a mutation policy.
Local branches use local parents; remote-only branches use same-remote parents.
`ready` means fast-forward possible into the parent; `merged` means the branch
tip is already in the parent. Divergence shows `conflicts possible`, an advisory
warning rather than a conflict prediction. Missing parents are marked explicitly.
`list` runs `git fetch -p` before inspection, using Git’s default remote
selection, and sorts by full committer timestamp, newest first. Fetch failures
stop the command; repositories without remotes are listed locally. Fetch can
update/prune remote-tracking refs and write objects/FETCH_HEAD, but does not
change local branches or working files. Before fetching, all configured
`remote.*.fetch` mappings must have explicit destinations under `refs/remotes/`
(or an empty destination). Unsafe mappings, including local mirror branches,
are rejected before any fetch. Automatic tag fetching and tag pruning are
disabled for listing. Other remotes may remain stale.

## Sync

```sh
gitex sync
gitex sync --dry-run
gitex sync --no-push
gitex sync feature/topic --path /path/to/repository
```

Sync defaults to the current branch and publishes safely ahead commits unless
`--no-push` or `workflow.push_on_sync=false` is set. Explicit CLI flags win.
It fetches only the configured upstream branch into its remote-tracking ref,
then fast-forwards a behind checkout or publishes an ahead checkout. It never
merges divergent histories or force-pushes. Plans show affected local/remote
refs before each stage; execution revalidates branch state after fetching and
before applying changes. Publication uses the tracked remote's fetch endpoint,
not a separate push URL or configured push refspec.

A clean checkout, supported branch policy and existing upstream are required.
Dirty/index changes, ongoing operations, divergence, missing upstreams and
branches checked out elsewhere block execution. This first implementation
supports the current checkout only; switch to a named branch before syncing it.
Publication from or to main/develop is blocked: use PR/MR review, or `--no-push`
for local synchronization. A rejected push preserves local commits for retry.

`--dry-run` uses cached refs only and never fetches or changes local/remote
state. Its plan may be stale. Fetching in normal mode may update objects,
FETCH_HEAD and the upstream tracking ref even when later work is blocked.

---
Licensed under the [MIT License](LICENSE).

Copyright (c) 2026 wnowicki.dev
