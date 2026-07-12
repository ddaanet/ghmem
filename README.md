# ghmem

Institutional-memory pipeline for the `micro` repository: pulls
GitHub PR/issue/discussion history into a local corpus so that
agentic sessions can query it instead of re-fetching from the
GitHub API on every run.

This is Part 1 (corpus store) of the ghmem subproject. It provides
the storage core only — an append-only event ledger and a SQLite
mirror rebuilt from it. Fetch (Part 2) and sync/query commands
(Part 3) are layered on top in later work.

## Development

```console
$ uv sync
$ just green   # format, ruff, mypy, pytest
```

`corpus/` data lives at the repository root (see `../../corpus/`),
not inside this tool tree — resolved via `ghmem.paths.corpus_dir()`,
overridable with `$GHMEM_CORPUS_DIR`.
