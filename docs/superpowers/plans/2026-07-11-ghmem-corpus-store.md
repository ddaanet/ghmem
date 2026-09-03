# ghmem Subproject 1 (corpus), Part 1 — Store

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Plan style.** Contract-style per the spec's Execution mandate:
> interface contracts, test lists, tricky algorithms — **not** full
> code listings. Implementers are Sonnet under the toolchain gates
> below with one-tier-up (Opus) review. Every task is TDD: write the
> failing test, watch it fail, implement, watch it pass, commit.
>
> **Split — Part 1 of three.** The corpus subproject exceeds the
> 400-line plan cap, so it splits on the spec's local-vs-network seam
> into three plans, landed in order: **Part 1 — Store** (this file:
> scaffold, ledger, SQLite mirror), **Part 2 — Read**
> (`…-corpus-read.md`: standing derivation, `show`), **Part 3 — Sync**
> (`…-corpus-sync.md`: the GitHub client and fetch driver). Parts 2
> and 3 consume this part's `Ledger`, `Store`, `FetchEvent` schema, and
> `aliases`.

**Goal:** Stand up the `ghmem` harness scaffold and its storage core —
a uv-managed Python CLI with an append-only ledger and a regenerable
SQLite mirror — provable without network access.

**Architecture:** Deterministic and offline. A generic `Ledger`
primitive persists append-only JSONL; `store` shreds the fetch ledger
into a gitignored SQLite cache queried by later parts. The routing
ledger's *infrastructure* is built here (same primitive); its
verdict/dispatch event catalog belongs to subproject 2.

**Tech Stack:** Python ≥3.14, uv, click, stdlib `sqlite3`, pytest +
pytest-mock, ruff (`select=ALL`), mypy strict.

## Global Constraints

Copied verbatim from the spec
(`docs/ghmem-design.md`); every task's
requirements implicitly include these.

- Python floor **>=3.14**; no stringified forward references (3.14
  makes them unneeded and undesirable — developer tooling, no backward
  compatibility owed).
- Toolchain held to the strictest bar, config taken from sibling
  `edify` (`/Users/david/code/edify/pyproject.toml` + justfile):
  uv-managed, click CLI, pytest (+pytest-mock), `ruff select=["ALL"]`
  with edify's curated ignore list, `mypy strict` + `extra_checks`,
  docformatter, a justfile `green` recipe.
- Storage layer is **stdlib `sqlite3`**, not an ORM.
- Storage: two append-only JSONL ledgers, SQLite is a derived cache.
  **Fetch ledger gitignored** (size); **routing ledger committed**
  (system's own memory). SQLite regenerable from the ledgers →
  gitignored.
- No agent ever calls the GitHub API; agents (later subprojects) read
  only what `ghmem` emits.
- Formula constants ship **provisional** and are calibrated in
  subproject 4 — never hip-shot final values.
- Hard cap on human-reviewed artifacts, this plan and every resulting
  PR alike: **400 lines**; anything longer is split (sole PR
  exception: pure code-reorg with no behavioral change).

---

## Spec-Fidelity Audit (Part 1 slice)

The spec assigns corpus this slice (§Execution 1): *"scaffold,
direnv-activated venv, fetch + routing ledgers with SQLite mirror,
`sync` (including PR diffs), standing derivation, `show`."* Part 1
covers the storage core:

- scaffold + direnv-activated venv → **T1**
- fetch + routing ledgers → **T2** (generic append-only primitive +
  fetch-event schema; the routing ledger's file and primitive are
  created here, but its verdict/dispatch/token/dirty-flag event
  catalog is **deferred to subproject 2** — corpus lays storage,
  dispatch writes verdicts)
- SQLite mirror → **T3**

**Deferred to later parts:** standing derivation and `show` (Part 2);
`sync` and the GitHub client (Part 3).

**Out of the whole subproject** (later subprojects, do not build):
`heat`/`triage`/`route`/`status`/`topic`/`close`/`index`, the two
agents + allowlist hook, `docs/design/` docs, calibration, backfill
runbook.

## File Structure

uv project rooted at `tools/ghmem/`, `src/`-layout package `ghmem`
(import name `ghmem`, distribution `ghmem-cli`, entry
`ghmem = "ghmem.cli:main"`).

Created in Part 1:

- `tools/ghmem/pyproject.toml`, `justfile`, `.envrc`, `.gitignore`,
  `README.md`
- `src/ghmem/__init__.py` — `__version__` (from `importlib.metadata`)
- `src/ghmem/aliases.py` — cross-cutting types: `Json`/`JsonObject`
  recursive aliases; `GraphQLId`/`Number` id `NewType`s
- `src/ghmem/cli.py` — click group; wires `rebuild`, `--version`
  (Parts 2–3 add `show`, `sync`)
- `src/ghmem/paths.py` — corpus dir + ledger/sqlite path resolution
  (env-overridable for tests)
- `src/ghmem/ledger.py` — generic append-only `Ledger[E]` + the
  `FetchEvent` schema
- `src/ghmem/models.py` — frozen dataclasses for query results
- `src/ghmem/exceptions.py` — domain exceptions (`ItemNotFoundError`,
  `PRDiffNotFoundError`; later parts add `RateLimitError`, `AuthError`)
- `src/ghmem/store.py` — SQLite schema, `rebuild_from_ledgers`, query
  API
- `tests/` (flat, mirrors edify), fixtures under `tests/fixtures/`

**Code/data separation** (ghmem has spinoff potential — its data must
not live inside the tool tree). Corpus data lives at repo root, *not*
under `tools/ghmem/`: `corpus/fetch.jsonl` (gitignored),
`corpus/routing.jsonl` (committed — the system's own memory belongs to
the consuming repo), `corpus/cache.sqlite` (gitignored). Default
resolves to `<git-toplevel>/corpus/`, overridable by
`$GHMEM_CORPUS_DIR` (tests point it at a tmp dir).

**Shared types** (`aliases.py`, defined in T1, used across all three
parts). JSON payloads use recursive aliases, never bare `object`:

```python
type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type JsonObject = dict[str, Json]
```

Ids use `NewType` for nominal distinctness (a `Protocol`/structural
type cannot tell two `int`s apart; `NewType` is the zero-cost tool
that makes `Number` reject a raw `int` and reject a `GraphQLId`):

- `GraphQLId = NewType("GraphQLId", str)` — opaque global node id,
  keys all internal relations.
- `Number = NewType("Number", int)` — issue/PR/discussion number, the
  human-facing lookup key.

mypy strict then rejects passing a number where an id is expected and
vice-versa.

---

### Task 1: Scaffold, toolchain gates, CLI skeleton

**Files:** Create `tools/ghmem/{pyproject.toml,justfile,.envrc,`
`.gitignore,README.md}`, `src/ghmem/__init__.py`,
`src/ghmem/aliases.py`, `src/ghmem/cli.py`, `src/ghmem/paths.py`,
`tests/test_cli_version.py`, `tests/test_paths.py`,
`tests/__init__.py`.

**Interfaces — Produces:**

- `ghmem.__version__: str` — `version("ghmem-cli")` from
  `importlib.metadata`. Version SPOT is pyproject's `[project].version`
  (edify pattern); do **not** hand-maintain a `VERSION` constant.
- `aliases`: `Json`, `JsonObject`, `GraphQLId`, `Number` (see Shared
  types above).
- `paths.corpus_dir() -> Path` (honors `$GHMEM_CORPUS_DIR`, else
  `<git-toplevel>/corpus`), `paths.fetch_ledger_path() -> Path`,
  `paths.routing_ledger_path() -> Path`, `paths.sqlite_path() -> Path`.
- `cli.main` — click group; `ghmem --version` via
  `@click.version_option(package_name="ghmem-cli", ...)` (edify
  pattern), printing the packaged version.

**Notes:** Distribution name `ghmem-cli`, import name `ghmem`, entry
`ghmem = "ghmem.cli:main"`. Copy edify's `[tool.ruff]`, `[tool.mypy]`,
`[tool.pytest.ini_options]`, `[tool.docformatter]` blocks verbatim;
`requires-python = ">=3.14"`; deps `click` (githubkit/pydantic added
in Part 3); dev deps `mypy`, `ruff`, `pytest`, `pytest-mock`.

`.envrc` is evaluated by direnv **in bash**, regardless of the user's
interactive shell (fish) — so use bash/direnv-stdlib syntax, never
`activate.fish`. Contents: `source_up_if_exists` (inherits the
repo-root envrc), then expose the uv venv without a shell-specific
`activate` script (`uv sync` creates `.venv`):

```bash
source_up_if_exists
export VIRTUAL_ENV="$PWD/.venv"
PATH_add "$PWD/.venv/bin"
```

`.gitignore` (repo-root, since `corpus/` is at repo root): add
`corpus/fetch.jsonl` and `corpus/cache.sqlite`; keep
`corpus/routing.jsonl` tracked (do NOT ignore the whole `corpus/`
dir). Provide a `green` justfile recipe (format → ruff → mypy →
pytest), mirroring edify's semantics.

**Steps:**

- [ ] Write `tests/test_cli_version.py::test_version_flag` (invoke
  `main` via click `CliRunner`, assert the packaged version in output)
  and `tests/test_paths.py::test_corpus_dir_env_override`.
- [ ] `uv init`/scaffold pyproject + configs so the package imports,
  then stub `cli.main` (empty group) and `paths.corpus_dir` (returns a
  deliberately wrong path). Run both tests; confirm each fails on its
  **assertion** — wrong version, wrong dir — not on ImportError or a
  missing command. A test that errors on a missing symbol has not
  proven it asserts anything.
- [ ] Fill in `aliases.py`, `__init__.__version__`, real `paths.py`,
  and `cli.main`'s `version_option` wiring; watch the assertions pass.
- [ ] `just green` — verify format+ruff+mypy+pytest all pass.
- [ ] Commit: `feat(ghmem): scaffold uv project, toolchain gates, CLI
  skeleton`.

---

### Task 2: Append-only ledger primitive

**Files:** Create `src/ghmem/ledger.py`, `tests/test_ledger.py`.

**Interfaces — Consumes:** `paths.fetch_ledger_path`,
`paths.routing_ledger_path`, `aliases.JsonObject`.

**Produces:** `class Ledger[E]` (PEP 695 generic), `Ledger(path:
Path)`, with methods:

- `append(event: E) -> None` — one JSON object per line; flush +
  `os.fsync` so an interrupted run keeps every completed line.
- `__iter__() -> Iterator[E]` — streams in append order; tolerant of a
  trailing partial line from a crash (yields only complete lines).
- `exists() -> bool`.

The type parameter — not `object` — carries the schema; each event kind
is a `TypedDict` (a plain `dict` at runtime), so `json.loads` results
satisfy `E` under a `cast(E, ...)` with no conversion.

**Fetch-event schema (defined here; written at runtime by Part 3's
sync driver, read by T3's store).** `FetchEvent` is a discriminated
union (sum type) over `type`; the fetch ledger is a
`Ledger[FetchEvent]`:

```python
class ItemEvent(TypedDict):
    type: Literal["item"]
    data: JsonObject  # raw GraphQL node
# CommentEvent, ReviewEvent, ReviewCommentEvent, ReactionEvent,
# CrossRefEvent, CommitEvent, PRDiffEvent — same {type, data} shape
class CursorEvent(TypedDict):
    type: Literal["cursor"]
    data: CursorData  # typed: connection, top_level_cursor, updated_at_hwm
class FetchFailedEvent(TypedDict):
    type: Literal["fetch_failed"]
    data: FetchFailedData  # typed: id, reason
type FetchEvent = (
    ItemEvent | CommentEvent | ReviewEvent | ReviewCommentEvent
    | ReactionEvent | CrossRefEvent | CommitEvent | PRDiffEvent
    | CursorEvent | FetchFailedEvent
)
```

Raw-node kinds carry `data: JsonObject` (the GraphQL node is genuinely
dynamic — `JsonObject` is the honest most-specific type);
ghmem-constructed kinds (`cursor`, `fetch_failed`) carry typed `data`.
The routing ledger reuses the same generic as `Ledger[RoutingEvent]`;
`RoutingEvent` is subproject 2's to define.

**Steps:**

- [ ] Write `tests/test_ledger.py`: `test_append_iter_roundtrip`
  (append 3 dicts → iter yields them in order), `test_reopen_appends`
  (new `Ledger` on same path appends after existing),
  `test_trailing_partial_line_ignored` (hand-write a file ending in a
  truncated line → iter yields only whole records).
- [ ] Stub `Ledger` importable but inert (`append` a no-op, `__iter__`
  yields nothing); run and confirm each test fails on its assertion
  (roundtrip/reopen/partial-line mismatch), not on ImportError.
- [ ] Implement `Ledger` and the `FetchEvent` schema.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): append-only JSONL ledger primitive`.

---

### Task 3: SQLite mirror — schema, rebuild, query API

**Files:** Create `src/ghmem/models.py`, `src/ghmem/exceptions.py`,
`src/ghmem/store.py`, `tests/test_store.py`,
`tests/fixtures/fetch_small.jsonl`. Modify `src/ghmem/cli.py` (add
`rebuild`).

**Interfaces — Consumes:** `Ledger`/`FetchEvent` (T2),
`GraphQLId`/`Number` (T1 `aliases`).

**Produces:**

`models.py` — frozen dataclasses; ids typed with the `NewType`s, never
bare `int`/`str`:

- `Item(id: GraphQLId, kind: str, number: Number, title, state,
  author: str, created_at: datetime, updated_at: datetime,
  merged_by: str|None, merged_at: datetime|None,
  author_association: str|None, body: str)`
- `Comment`, `Review(state, submitted_at)`, `ReviewComment`
- `Reaction(subject_id: GraphQLId, content, count)`
- `CrossRef(src_id: GraphQLId, dst_id: GraphQLId, kind)`
- `Commit(sha, author, authored_date, message)`
- `PRDiff(pr_number: Number, diffstat: tuple[FileStat, ...], diff_text,
  truncated: bool)`, `FileStat(path, additions, deletions)`

`exceptions.py` (created here; later parts add the github errors) —
`ItemNotFoundError(LookupError)`, `PRDiffNotFoundError(LookupError)`:
self-documenting, catchable as `LookupError` (the `KeyError`/
`IndexError` family), so `show` (Part 2) catches one branch cleanly.

`store.py` — `class Store`; `Store.open(path: Path) -> Store`;
`rebuild_from_ledgers(fetch: Ledger[FetchEvent], routing:
Ledger[RoutingEvent]) -> None` (drop+recreate schema, replay fetch
events into tables; routing replay is a no-op stub here, wired in
subproject 2 — leave a documented hook). Query methods:

- `get_item(number: Number) -> Item` — **raises `ItemNotFoundError`**,
  never returns `None` (a missing item is an error, not a value).
- `comments_for(item_id: GraphQLId) -> list[Comment]`
- `reviews_for(pr_id: GraphQLId) -> list[Review]`
- `review_comments_for(review_id: GraphQLId) -> list[ReviewComment]`
- `reactions_for(subject_id: GraphQLId) -> list[Reaction]`
- `cross_refs_for(item_id: GraphQLId) -> list[CrossRef]`
- `pr_diff_for(number: Number) -> PRDiff` — **raises
  `PRDiffNotFoundError`**.
- `commits() -> list[Commit]`
- `merges_before(d: date) -> list[Item]`
- `items_authored_by(user: str) -> list[Item]`

`cli.rebuild` — `ghmem rebuild` rebuilds SQLite from the two ledgers.

**Notes:** Schema DDL is a module constant; one table per kind, plus
`fetch_failed(id, reason)`. Rebuild must be idempotent (drop-if-exists
first) and dedup by id on replay (upsert / `INSERT OR REPLACE` keyed on
id) so Part 3's resume re-fetches don't double-insert. SQLite
regenerable ⇒ never the source of truth. `cursor` events are consumed
by Part 3's sync driver, not stored as a table row here. Collection
queries return `[]` for an absent parent (empty is a legitimate
value); only the single-object lookups raise.

**Steps:**

- [ ] Author `tests/fixtures/fetch_small.jsonl` (a couple items,
  comments, one PR diff, a cross-ref, a commit).
- [ ] Write `tests/test_store.py`: `test_rebuild_populates_tables`,
  `test_rebuild_idempotent` (rebuild twice → identical query results),
  `test_get_item_and_relations`, `test_get_item_missing_raises`
  (unknown `Number` → `ItemNotFoundError`), `test_pr_diff_roundtrip`,
  `test_pr_diff_missing_raises`.
- [ ] Stub `Store`/`models`/`exceptions` importable but inert
  (`rebuild_from_ledgers` a no-op, lookups return `None`/empty instead
  of raising); run and confirm each test fails on its assertion — empty
  tables, absent raise — not on a missing symbol.
- [ ] Implement `models.py`, `exceptions.py`, `store.py`; wire
  `cli.rebuild`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): SQLite mirror with rebuild-from-ledgers`.

---

## Self-Review

- **Spec coverage (Part 1 slice):** scaffold/venv (T1), fetch+routing
  ledger infrastructure (T2), SQLite mirror (T3). Standing/`show` are
  Part 2; `sync`/client are Part 3.
- **Type consistency:** `GraphQLId` vs `Number` `NewType`s keep the
  two id kinds unmixable — relations keyed by `GraphQLId`, user-facing
  lookups by `Number`, checked by mypy; `Ledger[FetchEvent]`'s
  `append`/`__iter__` and `Store`'s query API are the surfaces Parts
  2–3 consume; `ItemNotFoundError`/`PRDiffNotFoundError` are the single
  exception names for the lookup misses.
- **PR-size cap:** three commits map to reviewable slices; if any
  single task's diff approaches 400 lines at PR time, split the PR.

## Execution Handoff

Part 1 plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — fresh Sonnet subagent per task,
   Opus review between tasks (per SDD reviewer-tiering), fast
   iteration.
2. **Inline Execution** — execute tasks in this session with
   checkpoints.

After Part 1 lands, execute Part 2 (`…-corpus-read.md`) then Part 3
(`…-corpus-sync.md`). Which approach?
