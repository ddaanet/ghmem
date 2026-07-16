# ghmem Subproject 1 (corpus), Part 3 — Sync

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Plan style — read before implementing.** This plan is
> **contract-style** per the spec's Execution-structure mandate: it
> gives interface contracts, test lists, and the genuinely tricky
> algorithms — **not** full code listings. Implementers are Sonnet
> under the toolchain gates below with one-tier-up (Opus) review; they
> fill in the bodies. This deliberately departs from writing-plans'
> "complete code in every step" default. Every task is still TDD:
> write the failing test, watch it fail, implement, watch it pass,
> commit.
>
> **Split — Part 3 of three, and the last.** Part 1 (Store), Part 2
> (Read), and a follow-on discussions slice have **all landed on
> main**; Part 3 — the network-facing layer that fills the corpus from
> GitHub — is the only corpus work left, and consumes their
> `Ledger[FetchEvent]`, `Store`, `aliases`, and `exceptions.py` as-is.

**Goal:** Add `ghmem sync` — a resumable, rate-limit-aware,
GraphQL-batched fetch of micro's issues, PRs (with review threads and
diffs), discussions, and commits into the fetch ledger that Part 1's
`store` rebuilds.

**Architecture:** A thin `GitHubClient` wraps githubkit's GraphQL and
REST calls, validating each response into a caller-supplied msgspec
Struct and metering the rate limit. Two drivers paginate the
repository's connections, drain nested connections via explicit
follow-up queries, and append every node behind a parse-before-append
gate, with a resumable cursor event per drained page.

**Tech Stack:** Python ≥3.14, uv, click, githubkit (typed GraphQL +
REST, built-in retry), msgspec, SQLAlchemy 2.0, pytest + pytest-mock,
ruff (`select=ALL`), mypy strict.

## Global Constraints

Copied from the spec (`docs/superpowers/specs/2026-07-09-ghmem-design.md`)
plus constraints the landed code has since established; every task's
requirements implicitly include these.

- Python floor **>=3.14**; no stringified forward references (3.14
  makes them unneeded and undesirable — developer tooling, no backward
  compatibility owed).
- Toolchain held to the strictest bar, config taken from sibling
  `edify`: uv-managed, click CLI, pytest (+pytest-mock), `ruff
  select=["ALL"]` with edify's curated ignore list, `mypy strict` +
  `extra_checks`, docformatter, a justfile `green` recipe.
- **Docstring width gotcha —** `just green` has no fixed point for a
  one-line summary of 81–88 chars: ruff (line-length 88) accepts it,
  docformatter (`wrap-summaries=80`) reflows it to two, and ruff then
  rejects that with **D205**. Keep one-line summaries ≤80 total chars,
  or write a multi-line docstring with a blank line after the summary.
  The 80 is intentional; do not "fix" it by widening.
- **Wire validation is msgspec, not pydantic.** The spec says pydantic;
  the landed parse layer uses `msgspec.Struct`, so Part 3 unifies with
  it and adds no dependency. This plan overrides the spec's wording
  here.
- GitHub access via **githubkit** (the one new dependency Part 3 adds);
  token from `gh auth token` or `$GITHUB_TOKEN`. Shelling out to `gh
  api graphql` is rejected.
- Storage: two append-only JSONL ledgers, SQLite is a derived cache
  behind SQLAlchemy 2.0. **Fetch ledger gitignored** (size); **routing
  ledger committed**. SQLite regenerable from the ledgers →
  gitignored.
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
  **T1** (client) + **T2** (issues/PRs) + **T3** (discussions)

The spec's in-scope error-handling clauses land here: rate-limit
exhaustion mid-sync (persist cursor, exit with resume hint, never lose
data), GraphQL partial failures (log, skip, record fetch-failed for
retry). Out-of-slice error clauses (triage dispatch reclaim, close-out
race) belong to subproject 2.

## File Structure

Adds to the `tools/ghmem/` uv project built in Part 1.

Created in Part 3:

- `src/ghmem/github.py` — githubkit wrapper: `graphql`, `pr_diff_text`,
  rate-limit accounting
- `src/ghmem/sync.py` — fetch drivers: pagination, nested cursors,
  cursor events, resume; owns the per-query msgspec response Structs
- `tests/test_github.py`, `tests/test_sync.py`,
  `tests/fixtures/graphql_pages/*.json`

Modified in Part 3:

- `src/ghmem/cli.py` — add `sync`
- `src/ghmem/exceptions.py` — add `RateLimitError`, `AuthError`
- `src/ghmem/ledger.py` — **verify, do not extend.** Listed
  deliberately: a plan that omits a file gets implementers who never
  open it, and that gap cost the discussions slice an Important
  finding. `FetchEvent` already unions all twelve kinds sync emits, so
  **no new kind is expected**. Two checks guard the drivers, on
  different axes — keep them straight:
  - **Kind, static.** `Ledger.__iter__` is generic over `E` and returns
    `cast("E", json.loads(line))` — a static-only cast, no runtime
    check — so mypy never re-validates events on read. The T2/T3
    `append` literals are therefore the first place it tests a
    `type`/`data` envelope against the `FetchEvent` union. A missing or
    wrong kind surfaces there: fix `ledger.py`, never silence it with a
    `cast`.
  - **Node shape, runtime.** A misshapen `data` payload is msgspec's
    job, not mypy's (`data` is `JsonObject`, opaque to mypy):
    `ingest`'s `msgspec.convert(..., forbid_unknown_fields=True)`
    rejects it at rebuild. That is the parse-before-append contract
    below, exercised by `test_appended_nodes_satisfy_raw_contract`.

**Consumed from Parts 1–2** (not redefined here): `Ledger[FetchEvent]`
and the `FetchEvent` schema (`ledger.py`), `Store`
(`rebuild_from_ledgers` dedups on id), the `aliases` `Json`,
`JsonObject`, `GraphQLId`, `IssueOrPRNumber`, `DiscussionNumber`, and
`exceptions.py`.

## The raw-node contract, and parse-before-append

**The contract is `src/ghmem/ingest.py`'s module docstring plus its
`_Raw*` struct docstrings — that is the authority, not this plan.**
Read it before writing either driver. It fixes the camelCase key names,
the `__typename` Issue/PullRequest discriminant, the `{"login": ...}`
author convention (null → `"ghost"`) and its bare-string `_RawCommit`
exception, the **injected** relation ids that no real GraphQL node
carries (`itemId`, `prId`, `reviewId`, `subjectId`, `discussionId`,
`parentId`), and the wholly synthesized `cross_ref` / `pr_diff` shapes.
`forbid_unknown_fields=True` on the `_RawNode` base makes it exact: the
driver must select the declared keys and **no others**.

**Parse-before-append is mandatory.** Before appending any node, the
driver validates it against that node's `_Raw*` struct; a failure is a
sync error naming the call that produced it. The point is *where the
error surfaces*, not data loss — the ledger is JSONL, so a bad line can
be repaired or dropped by hand. Validating at fetch time makes a
malformed node the fetch's fault at the moment of fetching, not
something `rebuild_from_ledgers` raises days later with only a JSON
line to reason back from.

---

### Task 1: GitHub client wrapper

**Files:** Create `src/ghmem/github.py`, `tests/test_github.py`.
Modify `src/ghmem/exceptions.py` (add the github errors), `pyproject.toml`
(add `githubkit`).

**Interfaces — Consumes:** `JsonObject`, `IssueOrPRNumber` (Part 1
`aliases`); `msgspec.Struct`.

**Produces:**

- `class GitHubClient`; `GitHubClient.from_env() -> GitHubClient` —
  token from `$GITHUB_TOKEN`, else `gh auth token`, else raise
  `AuthError`.
- **Construct githubkit with `auto_retry=False`** (or a
  `RetryDecisionFunc` that declines on rate-limit errors while keeping
  the 5xx retries). Not optional: githubkit defaults `auto_retry=True`,
  whose `RetryRateLimit` reacts to a rate limit by **blocking** in
  `time.sleep(x-ratelimit-reset - now)` — up to a full hour on a
  primary limit. That silently swallows the persist-cursor-and-exit
  design below and hangs the sync instead.
- `graphql[R: msgspec.Struct](query: str, variables: JsonObject,
  response: type[R]) -> R` — one call, four obligations:
  - **Every query selects `rateLimit { cost remaining resetAt }`.**
    Verified 2026-07-15, not an inherited assumption: githubkit does
    zero cost accounting, and this is the only way to see the budget in
    **points** — GraphQL's real currency, where a call is not 1 point.
    `github.graphql()` returns the parsed `data` block only, so the
    budget must arrive inside `data`.
  - **Meter, then raise or proceed.** Read `rateLimit` from the raw
    `data`; if `remaining` has fallen below a safety-margin constant
    (e.g. one page's cost), raise `RateLimitError(reset_at)` rather than
    issue the next call.
  - **Convert into the caller's Struct** via `msgspec.convert(...,
    strict=True)`, validating the response at runtime.
  - **Return type is the query's own Struct, not `JsonObject`.**
    `type[R]` with `R: msgspec.Struct` type-checks cleanly under strict
    mypy. `variables` stays `JsonObject`, though: a GraphQL variable map
    is arbitrary JSON keyed by name and differs per query, so a second
    type param buys nothing.
- `pr_diff_text(number: IssueOrPRNumber) -> tuple[str, bool]` — REST
  diff endpoint with `Accept: application/vnd.github.v3.diff`,
  returning `(diff_text, truncated)`.
  - **Diff text only.** The per-file additions/deletions stats that
    `_RawPRDiff.files` wants come from the GraphQL `pullRequest.files`
    connection in T2, not here; the spec pins REST at ~1 call/PR
    precisely because it fetches only the text.
  - `truncated` is `True` when REST refuses an outsized diff. Discover
    its actual refusal signal (status and/or body) against the live API
    and encode that — do not guess.
- Added to `exceptions.py`: `class RateLimitError(Exception)` (carries
  `reset_at: datetime`; `Error` suffix per PEP 8, not
  `Exhausted`/`Exception`), `class AuthError(Exception)`.

**Notes:** Wrap githubkit's GraphQL + REST; rely on its built-in retry
for transient errors. Do not implement pagination here (that's T2/T3) —
this is a thin, per-call wrapper. Tests use pytest-mock to stub
githubkit responses; no network.

**Steps:**

- [ ] Write `tests/test_github.py`: `test_graphql_converts_into_struct`
  (mock a response → returns a populated instance of the passed
  Struct), `test_graphql_rejects_malformed_response` (a wrong-typed
  field raises rather than returning junk),
  `test_rate_limit_exhaustion_raises` (mock a response with `remaining`
  below margin → `RateLimitError` with correct `reset_at`),
  `test_pr_diff_text_sets_diff_accept_header`,
  `test_pr_diff_text_flags_truncated`, `test_from_env_prefers_env_token`.
- [ ] Run, verify fail.
- [ ] Implement `github.py`, extend `exceptions.py`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): githubkit client wrapper with rate-limit
  accounting`.

---

### Task 2: `sync` — issues & PRs driver

**Files:** Create `src/ghmem/sync.py`, `tests/test_sync.py`,
`tests/fixtures/graphql_pages/*.json`. Modify `src/ghmem/cli.py` (add
`sync`).

**Interfaces — Consumes:** `GitHubClient` (T1),
`Ledger[FetchEvent]`/`FetchEvent` (Part 1), `Store` (Part 1),
`RateLimitError`.

**Produces:**

- `sync(client: GitHubClient, fetch: Ledger[FetchEvent], *, resume:
  bool) -> SyncResult` (`SyncResult` = counts fetched + final cursor).
  T2 wires issues/PRs and commits; T3 extends this same entry point.
- `cli.sync` — `ghmem sync [--resume]`; on `RateLimitError`, persist
  cursor (already in ledger), print resume hint, exit 0.
- Per-query msgspec response Structs — issues page, PRs page,
  nested-connection follow-up — each passed as `graphql(...,
  response=<Struct>)`. These type the **wire**, not the ledger's
  schema; they are distinct from `ingest.py`'s `_Raw*` structs, which
  type what gets **appended**.

**Tricky algorithm — GraphQL batching with explicit nested cursors.**
One top-level query paginates `repository.issues` (then
`pullRequests`) ordered by `UPDATED_AT` ascending, `after:
<topLevelCursor>`. Each item node also selects the **first page** of
its nested connections (`comments`, `reviews`→`comments`,
`timelineItems` for cross-references, `reactions`, and for PRs
`files`), each with its own `pageInfo`. Because a hot thread's nested
connection can exceed one page:

```
for each top-level page P (persist top-level cursor after P drained):
    parse-then-append every item node (by kind), plus its first
      nested page (comments/reviews/reactions/xrefs/files)
    for each item whose nested connection C has pageInfo.hasNextPage:
        issue follow-up query keyed by (item id, C, C.endCursor)
          until drained, appending each nested node as it arrives
    for each PR on the page:
        diff_text, truncated = client.pr_diff_text(number)
        append pr_diff{prNumber, files (from the GraphQL files
          connection), diffText, truncated}
    append cursor event {connection, top_level_cursor, updated_at_hwm}
```

Note the injected ids: a comment carries `itemId`, a review `prId`, a
review comment `reviewId`, a reaction `subjectId`. GraphQL does not
supply these — the driver knows the parent it drained the connection
from and must attach them. `cross_ref` is synthesized entirely from
`timelineItems` (`srcId`/`dstId`/`kind`).

Resume: on `--resume`, read the last `cursor` event from the fetch
ledger and start the connection at that cursor (nested follow-ups
always re-drain from the item, so a mid-item crash re-fetches that
item's nested pages — append-only + Part 1's rebuild dedups on id).
Partial failure: a single bad/null node is logged, skipped, and
recorded as a `fetch_failed` event — never aborts the page.

**Commits come from GraphQL** (revised 2026-07-15; the earlier draft
said `git log` off the local `micro/` clone). Paginate
`repository.defaultBranchRef.target ... on Commit { history(first:
100, after:, since:) { nodes { oid author { name } authoredDate
message } } }`, appending `commit` events. Why: one code path instead
of two — `git log` needs a subprocess and a `--format` parser, and
can't participate in cursor/resume/`fetch_failed`/parse-before-append —
and it drops the hidden dependency on a clone being present, fetched,
and on the right branch. Cost is not the objection it appears: ~3530
commits = ~36 pages once, incremental via `since:` after, against
5000 points/hour. Two driver-side transforms, same class as the
injected ids: **`oid` → `sha`** and **`author { name }` → a bare
`author` string** (the GitActor exception to the login convention).
`GitActor.name` is nullable in the schema, but **zero of micro's 3530
commits have an empty author name** — build nothing for it;
parse-before-append turns a null into a loud sync error if one ever
appears.

**Notes:** Cursor lives only in the fetch ledger (gitignored,
regenerable) — never SQLite. Nodes are appended as `JsonObject`; the
ledger stays schema-agnostic and `store` re-parses on rebuild.

**Steps:**

- [ ] Record fixture GraphQL pages (`tests/fixtures/graphql_pages/`): a
  2-page issues connection where one item has a second nested comment
  page, one PR with a `files` connection, one deliberately-null node.
- [ ] Write `tests/test_sync.py`: `test_full_sync_appends_expected_nodes`,
  `test_appended_nodes_satisfy_raw_contract` (rebuild the store from
  the produced ledger — proves the emitted keys parse, which is the
  whole contract), `test_nested_comment_pagination` (the over-one-page
  thread yields all comments), `test_injected_relation_ids_attached`,
  `test_resume_from_cursor` (interrupt after page 1 → `--resume`
  continues from page 2, no gaps/dupes after rebuild),
  `test_malformed_node_fails_before_append` (parse-before-append: a bad
  node never reaches the ledger), `test_null_node_recorded_fetch_failed`,
  `test_pr_diff_appended` (stats from GraphQL, text from REST),
  `test_commit_history_maps_oid_and_author_name` (`oid` → `sha`,
  `author { name }` → bare string).
- [ ] Run, verify fail.
- [ ] Implement `sync.py`, wire `cli.sync`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): resumable GraphQL sync for issues and PRs`.

---

### Task 3: `sync` — discussions driver

**Files:** Modify `src/ghmem/sync.py`, `tests/test_sync.py`,
`tests/fixtures/graphql_pages/`.

**Interfaces — Consumes:** everything T2 consumes, plus T2's page-drain
helpers — reuse them, do not fork a parallel implementation.

**Produces:**

- Discussions folded into `sync()`: paginate `repository.discussions`,
  appending `discussion` and `discussion_comment` events. It is its own
  connection with its own number space — `DiscussionNumber` collides
  with issue/PR numbers and is deliberately kept separate.
- Per-query msgspec response Structs for the discussions page and its
  nested comment/reply follow-ups.

**Tricky part — replies and the orphan property.** A discussion's
`comments` connection yields top-level comments; each has its own
`replies` connection. Both `discussionId` and `parentId` are
**injected** — `parentId` is omitted/null on a top-level comment and
carries the parent comment's id on a reply. An **orphan reply** is one
whose `parentId` names a comment absent from the corpus.

Stated precisely so it is neither ignored nor overstated: **an orphan
has never been observed** — nothing has been fetched from GitHub yet.
It is reasoned from pagination: drain comments across pages, or drain a
`replies` connection whose parent was skipped as a failed node, and a
reply could reference a parent that never landed. The renderer side is
already settled — `show.render_discussion` puts orphans at top level
and raises `show.OrphanReplyWarning`, so nothing vanishes silently.

So this is a **property to verify, not a requirement to engineer
around**. Drain each discussion's comments fully before its replies, so
a parent is always already appended, then test that the property holds.
If `OrphanReplyWarning` ever fires on real data, that is a sync bug
with a real reproduction — design for it then, not now.

**Steps:**

- [ ] Add fixtures: a 2-page discussions connection; a discussion whose
  comments span two pages and whose second page carries a reply to a
  first-page comment (the exact shape that would strand a reply if
  ordering were wrong); one answered discussion.
- [ ] Extend `tests/test_sync.py`:
  `test_discussions_appended_with_injected_discussion_id`,
  `test_reply_carries_parent_id`,
  `test_cross_page_reply_parent_appended_first` (rebuild + render →
  **no `OrphanReplyWarning`**; this is the property),
  `test_answered_discussion_records_answer_id`,
  `test_discussion_resume_from_cursor`.
- [ ] Run, verify fail.
- [ ] Implement the discussions drain in `sync.py`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): sync discussions with nested replies`.

---

## Self-Review

- **Spec coverage (Part 3 slice):** `sync` incl. PR diffs (T2), resume
  + rate-limit persistence (T1+T2), partial-failure skip/record (T2),
  GraphQL nested cursors (T2+T3), discussions (T3), the githubkit
  client (T1). Together with Parts 1–2 this completes the whole corpus
  §Execution-1 slice.
- **Type consistency:** `graphql[R: msgspec.Struct](..., response=type[R])
  -> R` is the single client signature; `variables: JsonObject`;
  `RateLimitError`/`AuthError` go in Part 1's `exceptions.py`; the
  drivers consume `Ledger[FetchEvent]` unchanged and rely on
  `rebuild_from_ledgers`' id dedup for resume correctness. Two Struct
  families coexist by design: wire Structs (`sync.py`) type responses,
  `_Raw*` structs (`ingest.py`) type appends.
- **Deltas from the 2026-07-11 draft**, mostly because Parts 1–2 landed
  in between: pydantic → msgspec; `Number` → `IssueOrPRNumber`; stdlib
  `sqlite3` → SQLAlchemy; `pr_diff` → `pr_diff_text` returning text
  only, with file stats moved to the GraphQL `files` connection;
  `ledger.py` added to File Structure; parse-before-append promoted to
  a contract section; discussions split out of T2 into T3; commits
  moved from `git log` to GraphQL; `auto_retry=False` required after
  validating githubkit's actual rate-limit behavior.
- **PR-size cap:** three commits (client, issues/PRs driver,
  discussions driver) map to reviewable slices. T2 is heaviest; if its
  diff runs long, the commit-history drain is the next clean seam.

## Execution Handoff

Part 3 plan complete; every dependency has landed, so this is ready to
execute now. Two execution options:

1. **Subagent-Driven (recommended)** — fresh Sonnet subagent per task,
   Opus review between tasks (per SDD reviewer-tiering), fast
   iteration.
2. **Inline Execution** — execute tasks in this session with
   checkpoints.

Which approach?
