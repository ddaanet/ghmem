# ghmem Subproject 2 (dispatch), Part A — Heat + Routing foundation + `status`

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Plan style.** Contract-style per the spec's Execution mandate:
> interface contracts, test lists, tricky algorithms — **not** full
> code listings. Implementers are Sonnet under the toolchain gates
> below with one-tier-up (Opus) review. Every task is TDD: write the
> failing test(s), watch them fail, implement, watch them pass,
> `just green`, commit.
>
> **Split — Part A of three.** Subproject 2 (dispatch) splits into
> **Part A** (this file) — `heat`, the routing-replay foundation, and
> `status`; **Part B** — the session-budgeted `triage`/`route` loop with
> `--requeue`; **Part C** — `topic`/`close` + `index`. Part A depends
> only on subproject 1 (corpus, landed on main) and is fully testable
> against hand-authored fixture ledgers (no network).

> **As built — where the landed code diverges from this plan.** The
> plan is left as written; these are the places where following it
> would now mislead. Read the code, not this file, for the current
> contract.
>
> - **`heat` is one aggregate SQL query, not a Python loop.** The
>   "reuse the `Store` query methods in a loop over
>   `all_issues_and_prs()`" design under Task 1 was replaced by a
>   single correlated-subquery `select` in `heat_query.py`, with a
>   test pinning it to exactly one statement.
> - **`reactions` covers the whole thread, not just the body.** Task
>   1's "item-level only in v1" limitation was closed before
>   calibration, since widening it afterwards would have invalidated
>   the weights. Reactions on comments, reviews and review comments
>   are fetched and summed.
> - **`HeatScore` carries `title`.** Added so ranked output can name
>   each item without a second query.
> - **`xref_degree` is called `related_items`, and counts distinct
>   neighbours rather than `len(cross_refs_for(id))`.** A PR closing an
>   issue is recorded twice — as `closes` and as `cross_referenced` —
>   so the row count double-weights exactly the strongest links. Degree
>   is an edge count and would have been the honest name for the buggy
>   version; the fixed metric counts items, so it says so.
> - **`ghmem heat` emits CSV, not a formatted line per item.** Its
>   only consumer is analysis, which wants columns; the column
>   contract is in `tools/ghmem/README.md`.

**Goal:** Rank the corpus by heat, lay the routing-ledger→SQLite replay
foundation the later dispatch commands write through, and report
coverage with `status` — all reading only the local corpus.

**Architecture:** `heat` is a pure deterministic ranking over the
fetch-derived SQLite mirror. The routing foundation adds the SQLite
tables the routing ledger replays into (`Route`, `Dispatch`, `Topic`)
and one `_apply_routing_event` reducer, called both by
`rebuild_from_ledgers`'s replay and (in Parts B/C) by each live command
right after it appends its event — so a live write and a cold rebuild
can never diverge. `status` reads that state plus the fetch cursor.

**Tech Stack:** Python ≥3.14, uv, click, SQLAlchemy 2.0 over SQLite,
pytest + pytest-mock, ruff (`select=ALL`), mypy strict.

## Global Constraints

Copied verbatim from the spec
(`docs/ghmem-design.md`); every task's
requirements implicitly include these.

- Python floor **>=3.14**; no stringified forward references.
- Toolchain held to the strictest bar, config already established in
  `tools/ghmem` (uv-managed, click, pytest + pytest-mock,
  `ruff select=["ALL"]` with the curated ignore list, `mypy strict` +
  `extra_checks`, docformatter, a justfile `green` recipe). Verify each
  task with **`just green`** (format, ruff, docformatter -c, mypy,
  pytest, `check_line_limits.sh`).
- No agent ever calls the GitHub API; agents (subproject 3) read only
  what `ghmem` emits.
- Formula constants ship **provisional** and are calibrated in
  subproject 4 — never hip-shot final values. Mark each
  `# PROVISIONAL — calibrated in subproject 4`.
- **docformatter is 1.7.7** (global `~/.local/bin`, not `.venv`); do not
  `uv pip install docformatter`. Keep one-line docstring summaries
  ≤80 total chars (docformatter `wrap-summaries=80` vs ruff 88 dead
  zone → D205).
- Hard cap on human-reviewed artifacts, this plan and every resulting
  PR alike: **400 lines**; anything longer is split.

## Architecture decision — where routing state lives (settled)

**SQLite is the sole read backend.** The two ledgers stay source of
truth (`fetch.jsonl` regenerable from GitHub; `routing.jsonl` original,
committed — the system's memory). Every mutating dispatch command
(Parts B/C) does two writes per invocation: append the event to
`routing.jsonl` **first** (durable truth), then apply it to SQLite via
the shared `_apply_routing_event` reducer (a `merge` upsert, never a
rebuild). That same reducer is what `rebuild_from_ledgers` calls, so
live writes and cold rebuilds cannot diverge; a crash between the two
heals on the next `rebuild`. A fold-the-ledger-in-memory alternative
was rejected — it forks the query surface and can't express "next
untriaged item in heat order excluding dispatched/routed" as one join.
**Part A builds the replay half only** (reducer + rebuild HOOK); no Part
A command appends, so it exercises the reducer through
`rebuild_from_ledgers` over hand-authored routing ledgers.

## Spec-Fidelity Audit (Part A slice)

- **`ghmem heat`**: deterministic ranking by comment count, reaction
  count, distinct participants, cross-reference degree, recurrence span
  → Task 1 (provisional weights).
- **Routing ledger → SQLite** (spec Storage; the `ingest.py` HOOK) →
  Tasks 2–3. Part A defines the `dispatched` and `route` kinds; the
  union grows in B (`requeue`) and C (`snapshot`/`close`), each
  extending the reducer.
- **`ghmem status`**: counts (triaged / routed / no-signal / pending /
  dispatched), known-topics list, dirty subset, sync cursor → Task 4.
- **Interpretation flags.** (a) The spec lists both "dispatched" and
  "stuck"; without run-boundary tracking the ledger can't tell a
  live-session dispatch from a stranded one (both are a `Dispatch` with
  no `Route`), so Part A reports one **dispatched (awaiting route)**
  figure — the set `--requeue` reclaims — and defers the split. (b) A
  route marks its topic dirty; dirtiness from **new comments** on
  routed items is computed at assembly in Part C, not by the reducer.
- **Deferred (named, not dropped):** `triage`/`route`/`--requeue` (B);
  `topic`/`close`/`index`, the `Snapshot` model, and the close
  compare-and-set (C).

## File Structure

Adds to the `tools/ghmem/` uv project (subproject 1, landed).

Created:

- `src/ghmem/heat.py` — `HeatScore`, `HEAT_WEIGHTS`, `rank` (T1)
- `src/ghmem/status.py` — `StatusSummary`, `build_status`,
  `render_status` (T4)
- `tests/test_heat.py`, `tests/test_status.py`,
  `tests/test_routing_replay.py`, and fixture ledgers under
  `tests/fixtures/`

Modified:

- `src/ghmem/store.py` — `all_issues_and_prs` (T1); `routes`,
  `dispatched_unrouted`, `topics` (T4)
- `src/ghmem/ledger.py` — routing event schema + `NO_SIGNAL` (T2)
- `src/ghmem/models.py` — `Route`, `Dispatch`, `Topic` (T2)
- `src/ghmem/ingest.py` — `_apply_routing_event`, fill replay HOOK,
  drop the `RoutingEvent = JsonObject` placeholder (T3)
- `src/ghmem/cli.py` — `heat` (T1), `status` (T4)

**Consumed from subproject 1** (landed; exact names): `Store` with
`get_issue_or_pr`, `comments_for(item_id)`, `reviews_for(pr_id)`,
`review_comments_for(review_id)`, `reactions_for(subject_id)`,
`cross_refs_for(item_id)`. Entities are **detached ORM dataclasses**
(value `__eq__`): `IssueOrPR` (`id: GraphQLId`,
`number: IssueOrPRNumber`, `title`, `author`, `created_at: datetime`,
`updated_at`, …), `Comment`/`Review`/`ReviewComment` (each with
`author` + a timestamp — `created_at`/`submitted_at`/`created_at`),
`Reaction` (`content`, `count`), `CrossRef` (`src_id`, `dst_id`,
`kind`). `Base` is the declarative base (`MappedAsDataclass`,
`kw_only=True`) whose `type_annotation_map` already maps `GraphQLId`,
`IssueOrPRNumber`, and `datetime`. `Ledger[E]` and `FetchEvent`
(incl. `CursorEvent.data.connection`/`updated_at_hwm`) live in
`ledger.py`; `rebuild_from_ledgers(engine, fetch, routing)` in
`ingest.py`; path helpers in `paths.py`.

Test fixtures are JSONL ledgers: fetch fixtures are raw GraphQL nodes
(the `_Raw*` shapes in `ingest.py`/`raw_nodes.py`), routing fixtures the
new `RoutingEvent` dicts from Task 2. Rebuild with the
`_rebuilt_store(tmp_path, fetch_fixture, routing_fixture)` pattern from
`tests/test_store.py`.

---

### Task 1: `heat` — deterministic corpus ranking

**Files:** Modify `src/ghmem/store.py`, `src/ghmem/cli.py`. Create
`src/ghmem/heat.py`, `tests/test_heat.py`,
`tests/fixtures/heat_corpus.jsonl`.

**Interfaces — Consumes:** `Store.comments_for`, `reviews_for`,
`review_comments_for`, `reactions_for`, `cross_refs_for` (subproject 1).

**Produces:**

- `Store.all_issues_and_prs() -> list[IssueOrPR]` — every item, ordered
  by `number` (the enumeration base for ranking and, later, triage).
  Mirrors the existing `select(...).scalars()` list pattern in
  `store.py`.
- `heat.py`:
  - `@dataclass(frozen=True, slots=True) class HeatScore` — fields
    `number: IssueOrPRNumber`, `score: float`, `comments: int`,
    `reactions: int`, `participants: int`, `xref_degree: int`,
    `span_years: float`. The per-item metric breakdown travels with the
    score so `heat` output and subproject-4 calibration read the same
    record.
  - `HEAT_WEIGHTS: dict[str, float]` — one weight per metric key
    (`"comments"`, `"reactions"`, `"participants"`, `"xref_degree"`,
    `"span_years"`), each `# PROVISIONAL — calibrated in subproject 4`.
  - `rank(store: Store) -> list[HeatScore]` — score every item, sorted
    **score descending, `number` ascending** as the deterministic
    tie-break.

**Metric definitions (deterministic, per item).**

- `comments` = `len(comments_for(id))` + `len(reviews_for(id))` +
  Σ `len(review_comments_for(r.id))` over its reviews — total thread
  volume, not just top-level comments.
- `reactions` = Σ `r.count` for `reactions_for(id)`. **Item-level
  only** in v1 — reactions on individual comments are not summed;
  documented limitation, revisited in calibration.
- `participants` = size of the distinct-author set over
  `{item.author}` ∪ comment authors ∪ review authors ∪ review-comment
  authors.
- `xref_degree` = `len(cross_refs_for(id))` (src or dst; the store
  query already returns both directions).
- `span_years` = `(last − first).days / 365.25`, where the activity
  dates are `item.created_at` plus every comment `created_at`, review
  `submitted_at`, and review-comment `created_at`; `first` is
  `item.created_at`. A no-activity item spans `0.0`.

`score` = Σ `HEAT_WEIGHTS[k] * metric_k`. Per-item metrics reuse the
existing `Store` query methods in a loop over `all_issues_and_prs()`
(~3.8k items, in-process SQLite — a GROUP-BY optimization is deferred
unless calibration shows it matters).

**`ghmem heat` command.** One deterministic line per item, heat order:
`#{number}  score={score:.1f}  c={comments} r={reactions}
p={participants} x={xref_degree} span={span_years:.1f}y  {title}`.

**Steps:**

- [ ] Author `tests/fixtures/heat_corpus.jsonl`: a clearly-hottest item
  (many comments across comments+reviews+review-comments, several
  distinct authors, reactions, cross-refs, activity spanning years); a
  mid item; a cold item (author only, no activity, `span 0.0`); a
  second item cross-referenced by the hot one (to give it `xref_degree`
  without volume).
- [ ] Write `tests/test_heat.py` (When/Then docstrings):
  `test_rank_orders_hot_before_cold` (hottest item first, cold last);
  `test_tie_break_is_number_ascending` (two equal-score items order by
  number); `test_metrics_count_reviews_and_review_comments`
  (`comments` includes reviews + review comments, not just top-level);
  `test_participants_are_distinct` (a repeat author counts once);
  `test_cold_item_spans_zero_years`;
  `test_all_issues_and_prs_orders_by_number` (in `test_store.py`).
- [ ] Run, verify fail.
- [ ] Implement `Store.all_issues_and_prs`, `heat.py`, and the `heat`
  command.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): heat ranking over the corpus`.

---

### Task 2: Routing event schema + SQLite tables

**Files:** Modify `src/ghmem/ledger.py`, `src/ghmem/models.py`. (No
standalone test — the schema is exercised by Task 3's replay tests.)

**Interfaces — Produces (ledger.py):**

- `NO_SIGNAL: Final[str] = "no-signal"` — the reserved verdict; a topic
  name may never equal it.
- `class DispatchedData(TypedDict)` — `session: str`, `item_id: str`,
  `packet_chars: int`.
- `class DispatchedEvent(TypedDict)` — `type: Literal["dispatched"]`,
  `data: DispatchedData`.
- `class RouteData(TypedDict)` — `session: str`, `item_id: str`,
  `verdict: str` (a topic name or `NO_SIGNAL`), `note: str` (`""` for
  `NO_SIGNAL`).
- `class RouteEvent(TypedDict)` — `type: Literal["route"]`,
  `data: RouteData`.
- `type RoutingEvent = DispatchedEvent | RouteEvent` — **the union
  grows** in Parts B (`RequeueEvent`) and C (`SnapshotEvent`,
  `CloseEvent`). Placed beside `FetchEvent` for symmetry.

**Interfaces — Produces (models.py):** three declarative entities on
the existing `Base` (all `kw_only`; `GraphQLId`/`int`/`bool` already
map cleanly).

- `class Route(Base)` — `item_id: Mapped[GraphQLId]` (primary key),
  `verdict: Mapped[str]`, `note: Mapped[str]`, `session: Mapped[str]`.
  **Latest-wins**: keyed by `item_id`, so a re-route replaces the row
  (the ledger keeps the full history; the mirror keeps the current
  verdict).
- `class Dispatch(Base)` — `item_id: Mapped[GraphQLId]` (primary key),
  `session: Mapped[str]`, `packet_chars: Mapped[int]`. A row means the
  item was served to some session; its `packet_chars` is what Part B
  meters. "Dispatched-unrouted" is a `Dispatch` with no `Route`.
- `class Topic(Base)` — `name: Mapped[str]` (primary key),
  `dirty: Mapped[bool]`. Created the first time an item routes to it.

Table names derive from the class via the existing
`_camel_to_snake` `__tablename__` directive (`route`, `dispatch`,
`topic`). No foreign keys (consistent with the landed fetch entities);
`Route.item_id` is a bare id string that may reference an `IssueOrPR`.

**Steps:**

- [ ] Add `NO_SIGNAL` and the four routing TypedDicts + `RoutingEvent`
  union to `ledger.py`.
- [ ] Add `Route`, `Dispatch`, `Topic` to `models.py`.
- [ ] `just green` (mypy proves the union is well-formed and the
  entities compile; `Base.metadata` now includes the three tables).
- [ ] Commit: `feat(ghmem): routing event schema and SQLite tables`.

---

### Task 3: `_apply_routing_event` reducer + fill the replay HOOK

**Files:** Modify `src/ghmem/ingest.py`. Create
`tests/test_routing_replay.py`, `tests/fixtures/routing_fetch.jsonl`
(a few items to route against), `tests/fixtures/routing_events.jsonl`.

**Interfaces — Consumes:** `RoutingEvent`, `NO_SIGNAL` (Task 2);
`Route`, `Dispatch`, `Topic` (Task 2); `Session` (SQLAlchemy).

**Interfaces — Produces:**

- `_apply_routing_event(session: Session, event: RoutingEvent) -> None`
  — the single reducer applying one routing event to the SQLite mirror,
  called by both `rebuild_from_ledgers`'s replay and (Parts B/C) live
  commands after they append. Also drops the
  `type RoutingEvent = JsonObject` placeholder in favor of importing the
  real `RoutingEvent` from `ledger.py` (keep `store.py`'s
  `RoutingEvent` re-export working).

**Reducer semantics (the tricky part).** Dispatch on `event["type"]`:

- `"dispatched"` → `session.merge(Dispatch(item_id=…, session=…,
  packet_chars=…))`. Idempotent by `item_id`.
- `"route"` →
  1. Look up the **existing** `Route` for this `item_id`
     (`session.get(Route, item_id)`). If it exists and its `verdict` is
     a real topic (`!= NO_SIGNAL`), mark that **old** topic dirty — a
     re-route dirties the topic it left, per spec Error handling
     ("re-routing marks both old and new topics dirty").
  2. `session.merge(Route(item_id, verdict, note, session))` — replaces
     the row (latest-wins).
  3. If the new `verdict != NO_SIGNAL`, mark the **new** topic dirty
     (creating the `Topic` row if absent). `NO_SIGNAL` touches no topic.
- A private `_mark_topic_dirty(session, name)` helper does the
  get-or-create-then-set used in steps 1 and 3.

Fill the replay HOOK in `rebuild_from_ledgers`: replace the
`for _event in routing: pass` stub with
`for event in routing: _apply_routing_event(session, event)`, **after**
the fetch loop (items exist before routes reference them). Replay stays
idempotent: `merge` upserts by primary key, so rebuilding twice yields
identical rows; with no `close` events in Part A, every routed topic
ends `dirty=True` — correct, since nothing has been synthesized yet.

**Steps:**

- [ ] Author fixtures: `routing_fetch.jsonl` with items `#1 #2 #3`;
  `routing_events.jsonl` — `dispatched #1`, `route #1 → "softwrap"`,
  `dispatched #2`, `route #2 → NO_SIGNAL`, `dispatched #3` (left
  unrouted), then a **re-route** `route #1 → "wrapping"`.
- [ ] Write `tests/test_routing_replay.py` (rebuild a store from both
  fixtures, then assert via the Store queries added in Task 4 — order
  Task 4's store methods first if implementing strictly TDD, or assert
  via direct `select` in the test):
  `test_route_creates_topic_marked_dirty`;
  `test_no_signal_routes_but_creates_no_topic`;
  `test_reroute_marks_both_old_and_new_topic_dirty` (both `"softwrap"`
  and `"wrapping"` dirty; `#1`'s current verdict is `"wrapping"`);
  `test_dispatched_without_route_is_stranded` (`#3` has a `Dispatch`,
  no `Route`); `test_rebuild_is_idempotent` (rebuild twice → identical
  rows).
- [ ] Run, verify fail.
- [ ] Implement `_apply_routing_event` + `_mark_topic_dirty`, fill the
  HOOK, drop the placeholder.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): replay routing events into the SQLite mirror`.

---

### Task 4: `status` — coverage report

**Files:** Modify `src/ghmem/store.py`, `src/ghmem/cli.py`. Create
`src/ghmem/status.py`, `tests/test_status.py`, fixtures reusing Task 3's
shapes.

**Interfaces — Consumes:** `Store.all_issues_and_prs` (Task 1); the
routing tables (Task 2); `Ledger[FetchEvent]`, `CursorEvent`,
`fetch_ledger_path` (subproject 1).

**Interfaces — Produces:**

- `Store.routes() -> list[Route]` — every current verdict.
- `Store.dispatched_unrouted() -> list[Dispatch]` — dispatches whose
  `item_id` has no `Route` (a `NOT EXISTS`/left-join query).
- `Store.topics() -> list[Topic]` — every known topic, ordered by
  `name`.
- `status.py`:
  - `@dataclass(frozen=True, slots=True) class StatusSummary` — fields
    `total: int`, `routed: int`, `no_signal: int`, `triaged: int`,
    `pending: int`, `dispatched: int`, `topics: tuple[str, ...]`,
    `dirty: tuple[str, ...]`, `cursors: tuple[tuple[str, str], ...]`
    (connection → `updated_at_hwm`, sorted by connection).
  - `build_status(store: Store, fetch: Ledger[FetchEvent])
    -> StatusSummary`.
  - `render_status(summary: StatusSummary) -> str` — a compact
    multi-line report.

**Derivation.** From the routing tables:

- `routed` = `Route` rows with `verdict != NO_SIGNAL`;
  `no_signal` = rows with `verdict == NO_SIGNAL`;
  `triaged` = `len(routes())` (latest-wins, so one row per item).
- `dispatched` = `len(dispatched_unrouted())` — the reclaimable set
  (the "dispatched/stuck" figure; see the audit's interpretation flag).
- `pending` = `total −` |routed-or-no-signal item ids ∪ stranded item
  ids|. (`dispatched_unrouted` already excludes routed items, so the
  two id sets are disjoint and `pending = total − triaged −
  dispatched`.)
- `topics` = every `Topic.name`; `dirty` = the subset with `dirty` set
  — so triage (Part B) can consult the topic list before minting a
  near-duplicate, and synthesis (Part C) knows what to rebuild.
- `cursors` = the **last** `CursorEvent` per `data.connection` scanned
  from the fetch ledger (cursors live in the fetch ledger, not SQLite),
  each rendered as `connection → updated_at_hwm`.

**`ghmem status` command.** `Store.open(sqlite_path())`,
`Ledger(fetch_ledger_path())`, then
`click.echo(render_status(build_status(store, fetch)))`.

**Steps:**

- [ ] Author a fixture pair: a fetch ledger with a handful of items and
  at least one `cursor` event per connection; a routing ledger routing
  some to topics, one to `NO_SIGNAL`, leaving one dispatched-unrouted
  and one untouched (pending).
- [ ] Write `tests/test_status.py` (When/Then): `test_counts_partition`
  (`routed + no_signal + pending + dispatched == total`);
  `test_dirty_is_subset_of_topics`;
  `test_cursor_reports_last_per_connection` (two cursor events on one
  connection → the later `updated_at_hwm` wins);
  `test_render_status_lists_topics_and_counts` (string contains the
  counts and topic names); plus
  `test_dispatched_unrouted_excludes_routed` in `test_store.py`.
- [ ] Run, verify fail.
- [ ] Implement the three `Store` queries, `status.py`, and the
  `status` command.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): status coverage report`.

---

## Self-Review

- **Spec coverage.** `heat`→T1; routing→SQLite foundation→T2/T3;
  `status`→T4. `triage`/`route`/`--requeue` (B) and
  `topic`/`close`/`index` (C) named as deferred; the dispatched/stuck
  split and comment-driven dirtiness flagged with reasons.
- **Type consistency.** `HeatScore`/`StatusSummary` fields, the
  `_apply_routing_event` signature, and the `Route`/`Dispatch`/`Topic`
  columns match across their definition, reducer, and query/test uses;
  `RoutingEvent` is imported from `ledger.py` everywhere after T3.
- **`ledger.py` listed** (T2), per the subproject-1 plan-gap lesson.
- **Line cap.** ~440, over 400 by explicit exception as the sync plan
  was (438): four independently-green tasks, each far under any
  per-task budget; splitting `heat` into its own plan is the
  over-slicing the cap exists to weigh against, not toward.

## Execution Handoff

Subagent-driven per [[sdd-reviewer-tiering]]: fresh Sonnet per task,
Opus review each, Opus whole-branch at the end. Genuine-red TDD
([[genuine-red-not-missing-sut]]). PR-slicing at the end is the human's
call; the four commits are individually green.
