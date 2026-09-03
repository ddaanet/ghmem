# ghmem

Institutional-memory pipeline for the `micro` repository: pulls
GitHub PR/issue/discussion history into a local corpus so that
agentic sessions can query it instead of re-fetching from the
GitHub API on every run.

The living design is `docs/ghmem-design.md` at the repository root,
and it is the source of truth — this file describes what is built, not
what is intended.

## What exists today

The corpus subproject is complete: fetching from GitHub, an
append-only event ledger, the SQLite mirror rebuilt from it, and the
read commands over that mirror (`show`, `discussion`, `heat`,
`status`). The dispatch subproject — the metered `triage`/`route`
session loop that hands items to triage agents — is in progress; the
store already carries its routing tables and its write seam.

## Storage model

Two append-only JSONL ledgers are the sources of truth, and SQLite is
a derived cache of them that `ghmem rebuild` recreates from scratch.
The ledger→mirror arrow isn't strictly one-way, though: `rebuild` also
appends to `routing.jsonl` itself, one `reenter` event per route whose
item has moved past its recorded verdict — see `ghmem rebuild` below.

- `fetch.jsonl` — everything fetched from GitHub. Regenerable: delete
  it and re-sync.
- `routing.jsonl` — triage verdicts. **Not** regenerable, and
  committed: it is the system's memory of what has been decided.

Nothing reads the SQLite file as authoritative, so losing it costs
only the time to rebuild.

Corpus data lives at the repository root (`../../corpus/`), not
inside this tool tree — resolved via `ghmem.paths.corpus_dir()`,
overridable with `$GHMEM_CORPUS_DIR`.

## Commands

`ghmem sync` — fetch issues, PRs, discussions and commits into
`fetch.jsonl`. Always resumes from the last run's cursor, so
re-running after an interruption continues rather than restarting. On
rate-limit exhaustion it reports the reset time and exits 0; re-run to
continue.

`ghmem rebuild` — replay both ledgers into the SQLite mirror, then
append a `reenter` event for every item whose corpus activity has
moved past its recorded verdict, freeing its dispatch so it rejoins
the triage queue. Running it twice in a row reenters nothing the
second time. Because it writes to `routing.jsonl`, run it between
orchestration runs, not mid-session.

`ghmem show <number>` / `ghmem discussion <number>` — render one
thread as compact markdown. Issues/PRs and discussions have disjoint
number sequences, hence two commands: `5` can name both. Exits 1 if
the number is not in the corpus.

`ghmem status` — corpus coverage: item counts, known topics, the
dirty subset, and the sync cursor position.

`ghmem heat` — rank the corpus by heat, hottest first, as CSV on
stdout.

### `ghmem heat` CSV contract

One header row, then one row per item. Columns, in order:

| column | meaning |
| --- | --- |
| `number` | issue/PR number |
| `title` | issue/PR title (quoted when it contains a comma) |
| `score` | the weighted sum the ranking sorts on |
| `comments` | comments + reviews + review comments |
| `reactions` | reactions on the body **and on every reply** |
| `participants` | distinct authors across the whole thread |
| `related_items` | distinct items cross-referencing or referenced, either direction |
| `span_years` | whole days between first and last activity, over 365.25 |

Every metric is scoped to the whole thread, not to the body alone.
Ties in `score` break by `number` ascending, so the output is
deterministic.

The weights behind `score` live in `ghmem.heat.HEAT_WEIGHTS` and are
**provisional** — they are calibrated against the real corpus in the
validation subproject. Consumers should treat the per-metric columns,
not `score`, as the stable part of this contract.

## Development

```console
$ uv sync
$ just green   # ruff, mypy, docformatter, pytest, line limits
```

`just green` enforces a hard 400-line cap per file
(`scripts/check_line_limits.sh`); a file at the cap gets split, not
compressed.

Note that `docformatter` is not in the project venv — run the gate as:

```console
$ PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH" just green
```
