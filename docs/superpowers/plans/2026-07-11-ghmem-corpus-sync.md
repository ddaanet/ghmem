# ghmem Subproject 1 (corpus), Part 3 — Sync

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Plan style — read before implementing.** This plan is
> **contract-style** per the spec's Execution-structure mandate: it
> gives interface contracts, test lists, and the one genuinely tricky
> algorithm — **not** full code listings. Implementers are Sonnet
> under the toolchain gates below with one-tier-up (Opus) review; they
> fill in the bodies. This deliberately departs from writing-plans'
> "complete code in every step" default. Every task is still TDD:
> write the failing test, watch it fail, implement, watch it pass,
> commit.
>
> **Split — Part 3 of three.** The corpus subproject splits into
> **Part 1 — Store** (`…-corpus-store.md`), **Part 2 — Read**
> (`…-corpus-read.md`), **Part 3 — Sync** (this file), the
> network-facing layer that fills the corpus from GitHub. It **depends
> on Part 1** (independent of Part 2), which must land first: this plan
> consumes Part 1's `Ledger[FetchEvent]`, `Store`, the `FetchEvent`
> schema, the `aliases` (`Json`/`JsonObject`, `Number`), and extends
> its `exceptions.py`. Recommended landing order is 1 → 2 → 3.

**Goal:** Add `ghmem sync` — a resumable, rate-limit-aware,
GraphQL-batched fetch of micro's issues, PRs (with review threads and
diffs), discussions, and commits into the fetch ledger that Part 1's
`store` rebuilds.

**Architecture:** A thin `GitHubClient` wraps githubkit's GraphQL and
REST calls, validating each GraphQL response into a caller-supplied
pydantic model and metering the rate limit. The `sync` driver
paginates the repository's connections, drains each item's nested
connections via explicit follow-up queries, fetches PR diffs over
REST, and appends everything to the fetch ledger with a resumable
cursor event.

**Tech Stack:** Python ≥3.14, uv, click, githubkit (typed GraphQL +
REST, built-in retry), pydantic, stdlib `sqlite3`, pytest +
pytest-mock, ruff (`select=ALL`), mypy strict.

## Global Constraints

Copied verbatim from the spec
(`docs/superpowers/specs/2026-07-09-ghmem-design.md`); every task's
requirements implicitly include these.

- Python floor **>=3.14**; no stringified forward references (3.14
  makes them unneeded and undesirable — developer tooling, no backward
  compatibility owed).
- Toolchain held to the strictest bar, config taken from sibling
  `edify` (`/Users/david/code/edify/pyproject.toml` + justfile):
  uv-managed, click CLI, pytest (+pytest-mock), `ruff select=["ALL"]`
  with edify's curated ignore list, `mypy strict` + `extra_checks`,
  docformatter, a justfile `green` recipe.
- GitHub access via **githubkit**; token from `gh auth token` or
  `$GITHUB_TOKEN`. Shelling out to `gh api graphql` is rejected.
- Storage: two append-only JSONL ledgers, SQLite is a derived cache.
  **Fetch ledger gitignored** (size); **routing ledger committed**.
  SQLite regenerable from the ledgers → gitignored.
- No agent ever calls the GitHub API; agents (later subprojects) read
  only what `ghmem` emits.
- Hard cap on human-reviewed artifacts, this plan and every resulting
  PR alike: **400 lines**; anything longer is split (sole PR
  exception: pure code-reorg with no behavioral change).

---

## Spec-Fidelity Audit (Part 3 slice)

The spec assigns corpus this slice (§Execution 1): *"scaffold,
direnv-activated venv, fetch + routing ledgers with SQLite mirror,
`sync` (including PR diffs), standing derivation, `show`."* Part 3
covers `sync` and its client; Parts 1–2 covered the rest.

- `sync` including PR diffs, resumable, rate-limit aware,
  GraphQL-batched with nested cursors, partial-failure handling →
  **T1** (client) + **T2** (driver)

The spec's in-scope error-handling clauses land here: rate-limit
exhaustion mid-sync (persist cursor, exit with resume hint, never lose
data), GraphQL partial failures (log, skip, record fetch-failed for
retry). Out-of-slice error clauses (triage dispatch reclaim, close-out
race) belong to subproject 2.

## File Structure

Adds to the `tools/ghmem/` uv project built in Part 1.

Created in Part 3:

- `src/ghmem/github.py` — githubkit wrapper: `graphql`, `pr_diff`,
  rate-limit accounting
- `src/ghmem/sync.py` — fetch driver: pagination, nested cursors,
  cursor events, resume; owns the per-query pydantic response models
- `tests/test_github.py`, `tests/test_sync.py`,
  `tests/fixtures/graphql_pages/*.json`

Modified in Part 3:

- `src/ghmem/cli.py` — add `sync`
- `src/ghmem/exceptions.py` — add `RateLimitError`, `AuthError`

**Consumed from Part 1** (not redefined here): `Ledger[FetchEvent]`
and the `FetchEvent` schema (`ledger.py`), `Store`
(`rebuild_from_ledgers` dedups on id), the `aliases` `Json`,
`JsonObject`, `Number`, and `exceptions.py`.

**The raw-node key contract** every appended `FetchEvent["data"]` must
satisfy — camelCase field names, the `__typename` discriminant, the
`{"login": ...}`-with-`"ghost"` author convention and its
`_parse_commit` bare-string exception, and the injected relation ids
(`itemId`, `prId`, `reviewId`, `subjectId`) plus the synthesized
`cross_ref`/`pr_diff` shapes — is recorded as the module docstring of
`src/ghmem/ingest.py` (Part 1). The sync client (T2 below) must emit
exactly those keys per event `type`; it is the authoritative spec, not
this plan.

---

### Task 1: GitHub client wrapper

**Files:** Create `src/ghmem/github.py`, `tests/test_github.py`.
Modify `src/ghmem/exceptions.py` (add the github errors).

**Interfaces — Consumes:** `JsonObject`, `Number` (Part 1 `aliases`);
`pydantic.BaseModel`.

**Produces:**

- `class GitHubClient`; `GitHubClient.from_env() -> GitHubClient`
  (token: `$GITHUB_TOKEN` else `gh auth token`; raise `AuthError` if
  neither).
- `graphql[R: BaseModel](query: str, variables: JsonObject, response:
  type[R]) -> R` — **every query must select `rateLimit { cost
  remaining resetAt }`**; the wrapper reads `rateLimit` from the raw
  `data` for accounting (raising `RateLimitError(reset_at)` when
  `remaining` drops below a safety-margin constant, e.g. one page's
  cost, instead of issuing the next call), then validates the `data`
  block into the caller-supplied pydantic model and returns it. Return
  type is the query's own model, not `JsonObject` — `type[R]` with
  `R: BaseModel` type-checks cleanly under strict mypy and validates
  the response at runtime. `variables` stays `JsonObject`: a GraphQL
  variable map is arbitrary JSON keyed by name and differs per query,
  so a second type param buys nothing.
- `pr_diff(number: Number) -> str` — REST diff endpoint with
  `Accept: application/vnd.github.v3.diff`.
- Added to `exceptions.py`: `class RateLimitError(Exception)` (carries
  `reset_at: datetime`; `Error` suffix per PEP 8, not
  `Exhausted`/`Exception`), `class AuthError(Exception)`.

**Notes:** Wrap githubkit's GraphQL + REST; rely on its built-in retry
for transient errors. Do not implement pagination here (that's T2) —
this is a thin, per-call wrapper. Tests use pytest-mock to stub
githubkit responses; no network.

**Steps:**

- [ ] Write `tests/test_github.py`: `test_graphql_validates_into_model`
  (mock a response → returns a populated instance of the passed
  model), `test_rate_limit_exhaustion_raises` (mock a response with
  `remaining` below margin → `RateLimitError` with correct
  `reset_at`), `test_pr_diff_sets_diff_accept_header`,
  `test_from_env_prefers_env_token`.
- [ ] Run, verify fail.
- [ ] Implement `github.py`, extend `exceptions.py`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): githubkit client wrapper with rate-limit
  accounting`.

---

### Task 2: `sync` — fetch driver (pagination, nested cursors, resume, PR diffs)

**Files:** Create `src/ghmem/sync.py`, `tests/test_sync.py`,
`tests/fixtures/graphql_pages/*.json`. Modify `src/ghmem/cli.py` (add
`sync`).

**Interfaces — Consumes:** `GitHubClient` (T1),
`Ledger[FetchEvent]`/`FetchEvent` (Part 1), `Store` (Part 1),
`RateLimitError`.

**Produces:**

- `sync(client: GitHubClient, fetch: Ledger[FetchEvent], *, resume:
  bool) -> SyncResult` (`SyncResult` = counts fetched + final cursor).
- `cli.sync` — `ghmem sync [--resume]`; on `RateLimitError`, persist
  cursor (already in ledger), print resume hint, exit 0.
- Per-query pydantic response models (issues page, PRs page,
  discussions page, nested-connection follow-up), each passed as
  `graphql(..., response=<Model>)`.

**Tricky algorithm — GraphQL batching with explicit nested cursors.**
One top-level query paginates `repository.issues` (then
`pullRequests`, then `discussions`) ordered by `UPDATED_AT` ascending,
`after: <topLevelCursor>`. Each item node also selects the **first
page** of its nested connections (`comments`, `reviews`→`comments`,
`timelineItems` for cross-references, `reactions`), each with its own
`pageInfo`. Because a hot thread's nested connection can exceed one
page:

```
for each top-level page P (persist top-level cursor after P drained):
    append every item node's raw data to the fetch ledger (by kind),
      plus its first nested page (comments/reviews/reactions/xrefs)
    for each item whose nested connection C has pageInfo.hasNextPage:
        issue follow-up query keyed by (item id, C, C.endCursor)
          until drained, appending each nested node as it arrives
    for each PR on the page: client.pr_diff(number) -> append pr_diff
    append cursor event {connection, top_level_cursor, updated_at_hwm}
```

Resume: on `--resume`, read the last `cursor` event from the fetch
ledger and start the connection at that cursor (nested follow-ups
always re-drain from the item, so a mid-item crash re-fetches that
item's nested pages — append-only + Part 1's rebuild dedups on id).
Partial failure: a single bad/null node is logged, skipped, and
recorded as a `fetch_failed` event — never aborts the page. Commit log
comes from the local `micro/` clone (`git log`), not GraphQL, appended
as `commit` events.

**Notes:** Cursor lives only in the fetch ledger (gitignored,
regenerable) — never SQLite. The raw GraphQL nodes are re-serialized
to `JsonObject` when appended as fetch events (the ledger stays
schema-agnostic — `store` re-parses on rebuild). The response models
exist only to type and validate the wire responses inside the driver;
they are not the ledger's schema.

**Steps:**

- [ ] Record fixture GraphQL pages
  (`tests/fixtures/graphql_pages/`): a 2-page issues connection where
  one item has a second nested comment page, one PR, one
  deliberately-null node.
- [ ] Write `tests/test_sync.py`: `test_full_sync_appends_expected_nodes`,
  `test_nested_comment_pagination` (the over-one-page thread yields
  all comments), `test_resume_from_cursor` (interrupt after page 1 →
  `--resume` continues from page 2, no gaps/dupes after rebuild),
  `test_null_node_recorded_fetch_failed`, `test_pr_diff_appended`.
- [ ] Run, verify fail.
- [ ] Implement `sync.py`, wire `cli.sync`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): resumable GraphQL sync with nested cursors
  and PR diffs`.

---

## Self-Review

- **Spec coverage (Part 3 slice):** `sync` incl. PR diffs (T2), resume
  + rate-limit persistence (T1+T2), partial-failure skip/record (T2),
  GraphQL nested cursors (T2), the githubkit client (T1). Together
  with Parts 1–2 this completes the whole corpus §Execution-1 slice.
- **Type consistency:** `graphql[R: BaseModel](..., response=type[R])
  -> R` is the single client signature; `variables: JsonObject`;
  `RateLimitError`/`AuthError` are the exception names, added to Part
  1's `exceptions.py`; the driver consumes Part 1's
  `Ledger[FetchEvent]` and `FetchEvent` schema unchanged and relies on
  `Store.rebuild_from_ledgers`' id dedup for resume correctness.
- **PR-size cap:** two commits (client, driver) map to reviewable
  slices; the client/driver seam already splits cleanly if the driver
  diff grows.

## Execution Handoff

Part 3 plan complete; execute **after** Part 1 lands. Two execution
options:

1. **Subagent-Driven (recommended)** — fresh Sonnet subagent per task,
   Opus review between tasks (per SDD reviewer-tiering), fast
   iteration.
2. **Inline Execution** — execute tasks in this session with
   checkpoints.

Which approach?
