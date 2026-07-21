# ghmem dispatch Part B1 — the triage session loop

> **For agentic workers:** REQUIRED SUB-SKILL: use
> superpowers:subagent-driven-development to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Split — Part B1 of two.** Subproject 2 (dispatch) Part B splits into
> **B1** (this file) — the queue query, the session-budgeted
> `triage`/`route` loop, and `requeue`; **B2** — the solo-item diff
> truncation that makes the progress guarantee fit its budget. B1
> depends only on Part A (landed on main) and is fully testable against
> hand-authored fixture ledgers.

**Goal:** Serve corpus items to triage agents one packet at a time, in
heat order, under a CLI-enforced batch budget, and recover the ones a
crashed session stranded.

**Architecture:** `triage` mints a session token and serves the hottest
untriaged item; `route` records a verdict and serves the next under the
same token. The budget is metered CLI-side from `Dispatch` rows, never
by the agent. Reservation is a single conditional insert, so concurrent
sessions cannot be handed the same item.

**Tech Stack:** Python 3.14, click, SQLAlchemy 2.0 typed declarative,
msgspec, pytest. `just green` is the gate.

## Global Constraints

- Every source file stays under 400 lines
  (`scripts/check_line_limits.sh`, wired into `just green`).
- Docstring summaries stay at or under 80 total characters, or are
  written as real multi-line docstrings — docformatter's
  `wrap-summaries=80` against ruff's line-length 88 is a dead zone that
  has no fixed point in between.
- docformatter resolves from `~/.local/bin` (1.7.7), never from
  `.venv`. Do not `uv pip install docformatter`.
- Budget constants ship PROVISIONAL and are calibrated in subproject 4.
  Mark them as such inline, exactly as `heat.py`'s `HEAT_WEIGHTS` does.
- Expected outcomes exit 0. Exit codes are reserved for actual errors.

## File Structure

- **Create `src/ghmem/budget.py`** — the two provisional batch
  constants and nothing else. The policy half, mirroring `heat.py`.
- **Create `src/ghmem/triage.py`** — the session loop: mint a token,
  render, gate, reserve, emit. Owns the packet format.
- **Modify `src/ghmem/heat_query.py`** — extract the ranking query so a
  filtered variant reuses it rather than copying it.
- **Modify `src/ghmem/ledger.py`** — add `RequeueEvent` to the
  `RoutingEvent` union. *Listed deliberately:* a plan that omits a file
  means implementers faithfully never touch it, and Part A's discussion
  slice lost a whole type contract that way.
- **Modify `src/ghmem/ingest.py`** — a `requeue` branch in
  `apply_routing_event`.
- **Modify `src/ghmem/store.py`** — `session_usage`, `reserve_dispatch`,
  `stranded_dispatches`.
- **Modify `src/ghmem/exceptions.py`** — `TriageSessionError`.
- **Modify `src/ghmem/cli.py`** — `triage`, `route`, `requeue`.
- **Test:** `tests/test_triage.py`, `tests/test_queue.py`,
  `tests/test_requeue.py`, plus additions to `tests/test_store.py`.

---

### Task 1: The untriaged queue query

**Files:**
- Modify: `src/ghmem/heat_query.py`
- Test: `tests/test_queue.py`

**Interfaces:**
- Consumes: `heat_scores(engine, weights)` as it stands today.
- Produces:
  - `untriaged_heat_scores(engine, weights) -> list[HeatScore]`

**The refactor.** `heat_scores` builds its whole aggregate inline.
Extract that into a private `_ranked_query(weights)` returning the
`Select` **and** the `item` alias, so a caller can add a `WHERE` against
the same alias. `heat_scores` becomes a thin caller; the filtered
variant adds two exclusions.

**The filter.** Exclude any item carrying a `Route` row, or an
outstanding `Dispatch` row. Both as `~exists()` correlated on the item
alias.

**The trap this task walks into.** SQLAlchemy auto-correlation does not
reach inside a compound select or a nested `IN` — it silently
cross-joins the outer alias back into the subquery's own `FROM` and
returns plausible global aggregates. Every existing subquery in this
module calls `.correlate(item)` explicitly for that reason. The two new
`exists()` clauses need the same treatment. A fixture where only one
item has routes will *not* catch a missing correlation; give at least
two items routing state.

- [ ] **Step 1: Write the failing tests**

Four cases, against a fixture corpus of three items with distinct heat:
  - an unrouted, undispatched corpus returns every item, hottest first
  - an item carrying a `Route` is absent
  - an item carrying a `Dispatch` with no `Route` is absent
  - an item whose `Dispatch` was requeued (row deleted) is present again

- [ ] **Step 2: Run them and confirm they fail on the assertion**

`untriaged_heat_scores` must exist and return `[]` first. A failure that
reads `ImportError` is not a red test — stub the function, then assert.

- [ ] **Step 3: Extract `_ranked_query`, verified move-only**

Token-diff the moved SQL against `HEAD`: strip comments, newlines and
indentation, compare token streams. Identical but for the new return.
This check is stronger than "tests still pass" for a pure move.

- [ ] **Step 4: Implement the filter, then run `just green`**

- [ ] **Step 5: Prove the correlation has teeth**

Delete `.correlate(item)` from each new `exists()` in turn and confirm a
test dies. If a mutation survives, the fixture is too thin — widen it,
do not weaken the assertion.

- [ ] **Step 6: Commit**

---

### Task 2: Session metering and race-free reservation

**Files:**
- Modify: `src/ghmem/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Dispatch`, `Route` from `models.py`; `Ledger`,
  `DispatchedEvent` from `ledger.py`.
- Produces:
  - `Store.session_usage(session_token: str) -> tuple[int, int]`
    returning `(items, chars)`
  - `Store.reserve_dispatch(routing: Ledger[RoutingEvent],
    event: DispatchedEvent) -> bool`

**`session_usage`.** One aggregate over `Dispatch` filtered by
`session`: row count, and `coalesce(sum(packet_chars), 0)`. This is
exact rather than approximate *because* of Part A's keying decision —
`Dispatch` is keyed on `item_id` alone, concurrent sessions are handed
distinct items, and requeue runs only with no session live, so every
item a running session dispatched still carries that session here.

**`reserve_dispatch` — the tricky one.** Do **not** read-then-write. The
`sqlite3` driver opens a transaction implicitly before `INSERT`, but not
before `SELECT`, so a check in one statement and a write in the next is
unprotected: both processes read "free", both write. Collapse it into a
single conditional insert:

```python
stmt = (
    sqlite_insert(Dispatch)
    .values(item_id=..., session=..., packet_chars=...)
    .on_conflict_do_nothing(index_elements=["item_id"])
)
reserved = session.execute(stmt).rowcount == 1
```

SQLite allows one writer at a time and evaluates the conflict inside
that writer's transaction, so there is no window left to lock. On
`reserved`, append to the ledger and commit; otherwise roll back and
append nothing.

**Why the primary key is the whole contention point.** The other
exclusion — "not already routed" — cannot race: routing an item
requires it to be dispatched, which requires the row that would have
excluded it from the candidate list. Only `requeue` removes that row,
and it runs with no session live.

**Two properties to write into the docstring, or they get refactored
away.** First, this uses a conditional insert while
`apply_routing_event` uses an unconditional `session.merge` — replay
re-applies events that already happened and must not decline; the live
path must. They differ on purpose. Second, the ledger append sits
between the insert and the commit, so SQLite's writer lock serializes it
as a side effect; moving the append out of the transaction silently
gives that up.

Set `busy_timeout` explicitly on the engine rather than relying on
pysqlite's 5-second default.

- [ ] **Step 1: Write the failing tests**

  - `session_usage` sums chars and counts items for one token, ignoring
    another token's rows — give the two sessions *different* per-item
    char values, or a swapped-token mutation survives
  - `session_usage` on an unknown token returns `(0, 0)`
  - a reservation on a free item returns `True`, writes one ledger line,
    and leaves one `Dispatch` row
  - **the race:** two `Session`s on one engine, both reserving the same
    item; commit the first, then the second. The second returns `False`,
    appends nothing, and does not overwrite the first's `session` or
    `packet_chars`. No threads, no sleeps.

- [ ] **Step 2: Run them and confirm each fails on its assertion**

- [ ] **Step 3: Implement both methods**

- [ ] **Step 4: Run `just green`**

- [ ] **Step 5: Mutate to prove teeth**

Replace `on_conflict_do_nothing` with a plain insert and confirm the
race test dies. Return a hardcoded `True` and confirm it dies.

- [ ] **Step 6: Commit**

---

### Task 3: The triage session loop

**Files:**
- Create: `src/ghmem/budget.py`, `src/ghmem/triage.py`
- Modify: `src/ghmem/exceptions.py`, `src/ghmem/cli.py`
- Test: `tests/test_triage.py`

**Interfaces:**
- Consumes: `untriaged_heat_scores` (Task 1); `Store.session_usage`,
  `Store.reserve_dispatch` (Task 2); `show.render`,
  `standing.build_standing`, `Store.record_routing_event`.
- Produces:
  - `budget.BATCH_ITEMS: int`, `budget.BATCH_CHARS: int`
  - `triage.mint_session() -> str`
  - `triage.serve_next(store, routing, session_token) -> ServeResult`
  - `ServeResult` — a frozen dataclass carrying `packet: str | None` and
    `message: str | None`; exactly one is set.

**Budget constants.** `BATCH_ITEMS = 12`, `BATCH_CHARS = 200_000`, both
PROVISIONAL. Twelve comes from the spec's own reasoning: fixed spawn
overhead amortizes out around 5–12 items. The character cap is softer —
hot threads run 8–11k tokens each, so a hot batch ends on the character
gate after four or five items while a cold one runs to the item cap.
That asymmetry is intended, since heat order serves the biggest threads
first.

**Session token.** Eight characters drawn by `secrets.choice` from
`0-9A-Za-z`. 62⁸ ≈ 2.2×10¹⁴ against a lifetime of maybe 10⁴ sessions.
Not a UUID: a UUID's version and variant bits encode a structure nothing
here reads, and truncating one throws away the only property that
justified the format. `secrets` over `random` to sidestep any question
about seeding across processes the orchestrator spawns together.

**Serving order — render, gate, reserve.** The gate needs
`packet_chars`, which only the render produces. A packet failing the
gate is discarded with no `dispatched` event, which is exactly the
spec's "the batch ends with that item still queued". The item cap gates
*before* rendering, since it needs no size.

Walk the candidate list from Task 1 in order. For each: render, gate,
then `reserve_dispatch`. On `False` the item was taken by a concurrent
session — move to the next candidate and render that one. A collision
costs one wasted render, never a wasted triage.

**The first-item exception.** A session's first item is always served,
whatever its size — the progress guarantee, so no single item can
deadlock the queue. In B1 it is served oversized. **B2 is what truncates
its diff to fit.** Leave the branch named and commented as B2's hook;
do not stub a truncation that does nothing.

**Packet format.** `session: <token>`, `packet-chars: <n>`, the item
number, then `show.render`'s output.

**Terminal messages**, both to stderr with empty stdout and exit 0:
`batch complete: budget exhausted` and `triage complete: 0 pending`.

**`route <token> <number> <verdict> [note]`.** Records the verdict, then
serves the next packet under the same token. Three rejections, each
exit 1 on stderr via `TriageSessionError`:
  - a topic verdict with no note
  - `no-signal` with a note
  - a number not currently dispatched to this token

That last is the real guard: it catches an agent routing an id it was
never handed, or reusing a token after a requeue. It is a consistency
check against agent error, not authentication — the allowlist hook is
what constrains agents.

The CLI-facing id is the **item number**, matching `show <number>`; the
CLI maps it to a `GraphQLId` for the ledger.

**Known corner, accepted:** `triage` on an empty queue mints a token,
prints `triage complete: 0 pending`, and serves nothing, so no
`Dispatch` row ever carries that token and it is indistinguishable from
one invented. Harmless — the agent has nothing to route. Adding a
`session_opened` event purely to make an unusable token provably real is
not worth a union member. State this in `mint_session`'s docstring.

- [ ] **Step 1: Write the failing tests**

  - a fresh session serves the hottest item, with both header lines and
    the `show` rendering
  - `route` records the verdict and serves the *next* item, not a repeat
  - the item cap ends the batch: `BATCH_ITEMS` reached → stderr message,
    empty stdout, exit 0, and the next item still has no `Dispatch` row
  - the char cap ends the batch mid-run, with the gating item still
    queued
  - the first item of a session is served even when it alone exceeds
    `BATCH_CHARS`
  - a topic verdict with no note, `no-signal` with a note, and a number
    dispatched to a different token each exit 1
  - a candidate lost to a concurrent reservation is skipped and the next
    one served

  Patch the budget constants per test rather than building a 200 000
  character fixture.

- [ ] **Step 2: Run them and confirm each fails on its assertion**

- [ ] **Step 3: Implement `budget.py`, then `triage.py`, then the CLI**

- [ ] **Step 4: Run `just green`**

- [ ] **Step 5: Mutate to prove teeth**

At minimum: invert the gate comparison; drop the first-item exception;
serve the coldest candidate; skip the note validation. Each must kill a
test.

- [ ] **Step 6: Commit**

---

### Task 4: `requeue`

**Files:**
- Modify: `src/ghmem/ledger.py`, `src/ghmem/ingest.py`,
  `src/ghmem/store.py`, `src/ghmem/cli.py`
- Test: `tests/test_requeue.py`

**Interfaces:**
- Consumes: `apply_routing_event`, `Store.record_routing_event`,
  `Store.dispatched_unrouted`.
- Produces:
  - `RequeueData` / `RequeueEvent` in the `RoutingEvent` union
  - `ghmem requeue`

**Event shape.** `{"session": str, "item_id": str}`, **one event per
stranded item**. The reducer stays uniform — every routing event names
one item and touches one row. Carrying the abandoned session forward
matters to subproject 4: "this session stranded three items" is exactly
the signal that a batch budget was set too high.

**Reducer branch.** `requeue` **DELETEs** the item's `Dispatch` row.
This is the requirement Part A wrote into `Dispatch`'s docstring —
without the delete, a requeued item stays outstanding forever and is
never served again. Deleting an already-absent row is a no-op, not an
error, so replay stays idempotent.

**Its own command, not a `--requeue` flag.** The spec names a flag, and
this diverges deliberately: the caller is the orchestration script, not
an agent, and it runs before any agent exists. Serving a packet would
mint a token with nobody to continue it, leaving a live dispatch nobody
will ever route — recreating the exact stranded state requeue exists to
clear. `ghmem requeue` returns every stranded item, prints the count,
exits 0, and serves nothing.

The stranded set is exactly `Store.dispatched_unrouted()`, which Part A
already constrained to fetched items — so requeue is never handed an id
it could not build a packet for. Call it; do not add a second query.

- [ ] **Step 1: Write the failing tests**

  - replaying `dispatched` then `requeue` leaves no `Dispatch` row, and
    the item is a candidate again
  - `requeue` over a `dispatched` + `route` pair leaves the routed item
    alone — only *unrouted* dispatches are stranded
  - a `requeue` naming an item with no `Dispatch` row replays cleanly
  - the live path (`Store.record_routing_event`) and a cold rebuild from
    the same ledger agree on every observable row
  - `ghmem requeue` prints the count and exits 0

- [ ] **Step 2: Run them and confirm each fails on its assertion**

- [ ] **Step 3: Add the union member, the reducer branch, the command**

Adding a member without a reducer branch is caught by
`UnknownRoutingEventError`, not by mypy — the ledger line is untyped
JSON at runtime. Write the branch and the type together.

- [ ] **Step 4: Run `just green`**

- [ ] **Step 5: Mutate to prove teeth**

Turn the DELETE into a no-op and confirm the requeue test dies. This is
the mutation that matters: a silently-ineffective requeue is the failure
mode Part A predicted.

- [ ] **Step 6: Commit**

---

## Out of scope for B1

- **Solo-item diff truncation** — B2. The first-item branch exists here
  and serves oversized.
- **`topic` / `close` / `index`** — Part C.
