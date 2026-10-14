# ghmem

Institutional-memory pipeline for a GitHub repository: pulls its
PR/issue/discussion history into a local corpus so that agentic
sessions can query it instead of re-fetching from the GitHub API on
every run. It was built on `micro-editor/micro`, its first corpus.

The living design is `docs/ghmem-design.md`, in this repository's
`docs/` directory, and it is the source of truth — this file describes
what is built, not what is intended. It names the detail it carries:
command contracts in `docs/ghmem-harness.md`, the argument behind each
mechanism in `docs/ghmem-decisions.md`, refused designs in
`docs/ghmem-rejected.md`. `docs/ghmem-changelog.md` indexes
the dated record of each change to that design.

## What exists today

The whole pipeline runs. `sync` fetches issues, PRs, discussions and
commits from GitHub into an append-only event ledger; `rebuild`
replays both ledgers into the SQLite mirror; the read commands
(`show`, `discussion`, `heat`, `status`) query that mirror; and the
dispatch loop is complete — `triage`/`route` meter corpus items to
triage agents, `topic`/`close` hand a routed topic to a synthesis
agent, and `index` regenerates the topic index over the result.
`requeue` and `unroute` are its two correction paths.

What is outstanding is coverage, not machinery: most of the corpus is
still untriaged, and most topics still hold a single item.
`ghmem status` reports where that stands.

## Running it in a project

ghmem is a Claude Code plugin around a Python CLI, and for plugin use
nothing is installed by hand.

**The command.** It is a Python 3.14 package. The plugin builds its
own virtualenv, `.venv` in the plugin's directory, at session start:
its `SessionStart` hook runs `uv sync --frozen --no-dev` from
`uv.lock`, and where uv is missing or fails, falls back to
`python3 -m venv` and `pip install` of the pinned `requirements.txt`
(generated from `uv.lock` by the gate), then of the plugin's tree
without dependencies. The fallback is slower and needs a `python3` of
3.14 or newer on PATH, which uv would otherwise fetch. It prints
one line saying it built, or why it could not, with the reason in
`.venv-build.log` and the agent told not to dispatch the ghmem agents,
and builds nothing when `ghmem` already runs. A `.venv` it did not
build, a development tree's own, is reported when broken and never
removed. The plugin's `bin/ghmem` launcher runs the CLI from that
environment, and Claude Code puts `bin/` on the Bash tool's PATH once
the plugin is enabled from a marketplace, which is where the agents
look: they call `ghmem` by bare name. A plugin update lands in a fresh
directory and builds a fresh environment from its own `uv.lock`.

A session loaded with `CLAUDE_CODE_PLUGIN_DIRS`, as the dev shim does,
gets no plugin `bin/` on PATH from Claude Code. The repository's
dogfood shim adds it, last. A `ghmem` already on PATH ahead of it, such as
this checkout's direnv-activated `.venv`, wins, and the plugin then builds no
environment of its own.

To use the CLI outside the plugin, install it as a uv tool:

```console
$ uv tool install git+https://github.com/ddaanet/ghmem
```

That resolves from `pyproject.toml` and ignores `uv.lock`, so the
version ranges declared there are what it gets.

**The project.** Run `ghmem` from inside the git tree whose history
the corpus belongs with. It needs one file, `corpus/target.toml`,
naming the repository to mirror (`owner`, `repo`, `owner_login`), and
a GitHub token for `sync` — `$GITHUB_TOKEN`, else `gh auth token`.
Everything else it creates. Ignore `corpus/fetch.jsonl`,
`corpus/cache.sqlite` and `corpus/packets/`; commit
`corpus/routing.jsonl` and `docs/design/`.

**The agents.** Install the plugin from the `ddaanet` marketplace:

```
/plugin install ghmem@ddaanet
```

Dispatch `ghmem:ghmem-triage` when `ghmem status` shows a backlog, and
`ghmem:ghmem-synthesize` once per dirty topic, each by `subagent_type`
with no further prompt needed. Both read text anyone can file on
GitHub, so both are contained by the plugin's hooks: Bash is held to
`ghmem` invocations, the triage agent's Read to `corpus/packets/`, and
the synthesis agent's Read, Write and Edit to `docs/design/`. Each agent
opens by checking that, with a Bash `echo` and a Read of `.gitignore`
that must both be refused, and reports `UNCONTAINED` and stops if
either is not.

The hooks need only a `python3` on PATH. They run from the plugin's
own files (`src/ghmem/hook.py`), so they depend neither on the `ghmem`
command nor on that environment being built. They run on every Bash,
Read, Write and Edit call of a session that enables the plugin,
passing through any that is not one of the two agents'. A hook fails
open, silently, only while it cannot tell whose call it is: a payload
that does not parse, no `python3`, or a plugin tree without its hook
module. Once it has identified one of the two agents and then fails,
it blocks that call, which stops those agents and no one else. On a
machine where `python3` is missing or cannot run the module the plugin
says so at session start, and again after resume, clear and compact;
do not dispatch the agents until it can.

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

Corpus data lives in the consumer project the CLI is run in
(`<git toplevel>/corpus/`), never in this repository — resolved via
`ghmem.paths.corpus_dir()`, overridable with `$GHMEM_CORPUS_DIR`.
Topic docs go to `docs/design/` in the same project. The data for
`micro-editor/micro` lives in the micro-craft repo.

Served triage packets are written to `corpus/packets/` (gitignored
scratch), and the triage agent's Read hook scopes to exactly that path
under `$CLAUDE_PROJECT_DIR`. `$GHMEM_CORPUS_DIR` and `$GHMEM_PACKET_DIR`
are test-only overrides: the hook honors neither, so a contained worker
run under either is unsupported.

Which repository a corpus mirrors is recorded beside it, in
`target.toml` (`owner`, `repo`, and `owner_login` — the login standing
ranks as owner). `sync` and the commands that render standing read it;
a missing file or key is an error naming both, never a default.

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

`ghmem requeue` — free every item a crashed session dispatched but
never routed, so it rejoins the triage queue. Like `rebuild` it writes
to `routing.jsonl`, so it belongs before an orchestration run starts
any triage session, never mid-session: serving a stranded item while a
session still holds it recreates the exact state this command clears.
It requeues, prints the count, and serves nothing.

`ghmem show <number>` / `ghmem discussion <number>` — render one
thread as compact markdown. Issues/PRs and discussions have disjoint
number sequences, hence two commands: `5` can name both. Exits 1 if
the number is not in the corpus.

`ghmem status` — corpus coverage. One counts line
(`total triaged routed no_signal pending dispatched`), then the known
topics, the dirty subset awaiting synthesis, and one cursor line per
sync connection.

`ghmem heat` — rank the corpus by heat, hottest first, as CSV on
stdout.

`ghmem triage` — mint a session token and print its first packet on
stdout. The token rides in the packet header; every item after the
first arrives from `ghmem route`, not from a second `triage` call. An
empty or fully-routed queue is not a failure: the terminal message
(`triage complete: 0 pending`, `batch complete: budget exhausted`,
`batch complete: all candidates taken`) goes to stderr and the exit
status is still 0. The `triage complete:` prefix means the corpus is
done and no further session is worth spawning; `batch complete:` means
only that this worker stops.

`ghmem route <token> <number> <verdict>` — record one verdict, then
serve the next packet in the same call. The note arrives on stdin,
fed by a heredoc whose delimiter is quoted; there is no argument form,
because a note is prose and a quoting mistake corrupts it silently. A
note is required for every verdict, `no-signal` included, and it must
be a single line. A rejected verdict — no note, a multi-line note, or
a NUMBER not currently dispatched to TOKEN — prints to stderr, exits
1, and leaves the ledger untouched. Otherwise it behaves exactly like
`triage`: a packet or a terminal message, exit 0 either way.

`ghmem unroute <number>...` — withdraw each number's verdict, clearing
both its route and its outstanding dispatch so it is served afresh.
This is the path for a verdict reached *wrongly*; a verdict the corpus
has merely moved past is `rebuild`'s job. Nothing is edited out of
`routing.jsonl` — the superseded `route` event stays there, session
and note included. Every number is resolved against the corpus before
any event is appended, so a typo at the end of a long batch leaves no
half-applied correction, and a number carrying no verdict is not an
error, which makes re-running the same batch a no-op. It takes no
session token, and an item it frees rejoins the queue rather than
becoming routable on the spot.

`ghmem topic <name>` — assemble one topic's synthesis context behind a
freshly minted snapshot token: a `snapshot: <token>` header, an item
index hottest-first, and every routed item's full thread. The token is
what `ghmem close` later compares against, so a synthesis pass reads
the context and closes with the token that came with it. A name with
no routed items exits 1 with the ledger untouched.

`ghmem close <token>` — record that token's synthesis as complete and
regenerate `docs/design/INDEX.md`. A compare-and-set against the
snapshot: if the topic changed since the snapshot was taken, nothing
is written and `stale: …` goes to stderr with exit 0; re-run
`ghmem topic` to pick up the change. A token naming no snapshot at all
is an error: stderr, exit 1, ledger untouched.

`ghmem index` — regenerate `docs/design/INDEX.md` from the topic docs'
frontmatter and the routing mirror. `close` already does this, so
calling it directly is for after a topic doc is edited by hand. It
creates `docs/design/` if it does not exist; an empty index is a valid
state, not an error.

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

The weights behind `score` live in `ghmem.heat.HEAT_WEIGHTS`. They
were calibrated on 2026-07-25 against 198 read-only signal labels and
**retained**: no fitted vector ranked better than the original, and
the fitted vector lost to it under cross-validation. Only `comments`
has a coefficient that calibration could resolve — the other four sit
inside their own minimum detectable effect — so consumers should still
treat the per-metric columns, not `score`, as the stable part of this
contract. The micro-craft repo records the fit and its reasoning, and
reweighting reorders topic-name minting, which `record_verdict` makes
permanent.

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
