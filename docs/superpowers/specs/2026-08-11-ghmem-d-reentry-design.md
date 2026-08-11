# ghmem dispatch Part D — re-entry on corpus change

Design spec. Lets a routed item whose GitHub thread changes — a new
comment, a state flip — return to the triage pending pool automatically,
so the next sync-triage-synthesize cycle re-judges it and refreshes its
topic doc. Replaces the manual "append a corrected `route` event plus
`rebuild`" workaround, and the unenforceable "revisit if the PR lands"
notes triage was writing by hand in their place.

Parent spec: `2026-07-09-ghmem-design.md` §Architecture (the `route`
reducer branch, latest-wins `Route`, "re-routing marks both old and new
topics dirty") and §Error handling. B1 policy reference:
`2026-07-21-ghmem-dispatch-triage.md` (`record_verdict` rejects a
live re-route as `already_routed` — deliberate, and explicitly scoped as
"out of scope until a later part asks for it"). This is that part.

## Motivation

Two things forced this design rather than leaving the manual workaround
in place. First, the workaround itself: correcting a landed verdict
today means hand-appending a `route` event straight to the ledger
(bypassing `record_verdict`'s guard, which only fences the session-loop
path) and running `rebuild` — used for #1445 (reopened out of
`no-signal` after `docs/ghmem-tail-sample.md` showed the original call
wrong) and for #3795/#3783. Every one of those was a human deciding, by
hand, that a specific item's thread had moved past its verdict. Nothing
in the system notices that on its own.

Second, triage had started writing "revisit if the PR lands" into
routing notes for exactly this situation — a verdict that's only
provisionally settled. That note is unenforceable: nothing reads it back,
nothing schedules the revisit, and it decays into a note nobody acts on.
Re-entry replaces the note with a mechanism: the corpus, not a
hand-written reminder, is what decides an item needs a second look.

## Central call — why this needs no live/replay compare-and-set

C2's `close` faced a similar-looking problem — "is this still current?"
— and resolved it by deciding the verdict live and recording only clean
outcomes, because `rebuild_from_ledgers` replays the fetch ledger to its
*final* state before the routing ledger, so recomputing currency at
replay time would reach a different answer than the live check did.

Re-entry doesn't have that hazard, for two different reasons on its two
halves:

- **Staleness itself is never reducer state.** `untriaged_heat_scores` is
  a read query, evaluated fresh against whatever the mirror currently
  holds — it isn't part of ledger replay and has no ordering dependency
  on it. Comparing `Route.item_updated_at` (a value fixed at the
  `route` event's write time, immutable through any replay) against the
  live `IssueOrPR.updated_at` gives the same answer whether the mirror
  was built incrementally or rebuilt from scratch, because both sides of
  the comparison are already final by the time any query runs. No event
  needs to carry a verdict, because the query recomputes it correctly
  every time.
- **`ReenterEvent` carries no verdict to go stale.** It doesn't assert
  "this item is stale" — the query already decided that. It's a
  mechanical instruction, "free this item's dispatch and dirty its
  topic," applied unconditionally on replay exactly as it was applied
  live. That makes it closer in shape to `RequeueEvent` (also a
  no-verdict, unconditional free) than to `CloseEvent`.

So the design needs no compare-and-set: the query is safe to recompute
anywhere, and the event has nothing in it that recomputation could
contradict.

## Ledger extension

`RouteData` gains one field, and one new event joins the union.

```python
class RouteData(TypedDict):
    """A triage verdict for one item, to a topic or to `NO_SIGNAL`."""

    session: str
    item_id: str
    verdict: str
    note: str
    item_updated_at: str  # ISO 8601, the item's updated_at at verdict time


class ReenterData(TypedDict):
    """A routed item whose corpus activity moved past its verdict."""

    item_id: str


class ReenterEvent(TypedDict):
    """A ghmem-constructed record that a stale route was reopened."""

    type: Literal["reenter"]
    data: ReenterData
```

`RoutingEvent` becomes `DispatchedEvent | RouteEvent | RequeueEvent |
SnapshotEvent | CloseEvent | ReenterEvent`, and `"reenter": ReenterEvent`
joins `_ROUTING_BY_TYPE`. `item_updated_at` is stamped from the
corpus item's live `updated_at` wherever a `RouteEvent` is constructed
(`record_verdict`, and any future re-route path) — required, not
optional, for every event built from here on. `RequeueData` stays
untouched: it still carries only `session`/`item_id`, so subproject 4's
abandonment-calibration signal reads exactly the entries it always has,
uncontaminated by corpus-driven frees.

`Route` gains a matching column: `item_updated_at: Mapped[datetime]`,
not nullable — see Rollout below for how a pre-migration row still gets
a value.

## Rollout — a cutoff constant, not a backfill

Hundreds of items are already routed, many already synthesized. Two
ways to give their `Route` rows a baseline:

- **Backfill by appending a corrected `route` event per item** —
  the pattern already used for #1445/#3795/#3783. It reuses the existing
  reducer unchanged, but at this scale it's the wrong tool: the `route`
  branch dirties both the topic an item leaves and the one it joins, and
  a same-topic backfill re-route would dirty *every* topic with a routed
  item, forcing a full re-synthesis pass across the corpus on rollout
  day for no actual content change. That cost was invisible at
  five hand-verified rows; it isn't at corpus scale.
- **A fixed rollout-cutoff constant, no backfill at all (chosen).** The
  reducer's `route` branch, replaying a pre-migration `RouteData` that
  lacks `item_updated_at`, stamps the row with a fixed constant (the
  date this feature ships) instead of leaving it null. Every
  already-routed item is then "current as of rollout" without a single
  extra ledger entry or a single extra dirty flag: only real activity
  *after* that date makes it stale.

This is a deliberate, scale-driven departure from the backfill-by-event
pattern, not a rejection of it — the pattern still stands for
hand-verified individual corrections, where there is no bulk-dirty cost
to worry about.

## Query change

`untriaged_heat_scores`'s route exclusion moves from "any `Route`
excludes" to "only a *fresh* `Route` excludes":

```python
query = query.where(
    ~exists().where(
        Route.item_id == item.id,
        Route.item_updated_at >= item.updated_at,
    )
).where(~exists().where(Dispatch.item_id == item.id))
```

The dispatch exclusion is untouched and still load-bearing: a stale
`Route` alone doesn't make the item reappear, because the original
triage session's `Dispatch` row for it was never deleted (`route` never
touches `Dispatch`). Freeing that row is `ReenterEvent`'s job.

## Reducer branch

`apply_routing_event` gains a `"reenter"` branch: delete the item's
`Dispatch` row if present (a no-op, not an error, if already absent —
same idempotence contract as `requeue`'s delete), then mark the item's
*current* `Route.verdict` topic dirty, skipping the no-op case where
that verdict is `NO_SIGNAL` and there is no topic to dirty. No change to
`Route` itself — staleness is never written, only read.

## Where the detection pass runs

Per the "fold into sync, not a separate command" decision: `ghmem sync`
gains a step, after its existing fetch pass, that walks every item
currently carrying a `Route` and appends a `ReenterEvent` for any whose
live `updated_at` has moved past its stamp and that doesn't already have
a newer, still-unactioned `ReenterEvent` — the idempotency guard that
keeps a repeatedly-stale item from being flagged again every sync cycle
before it's re-triaged. The exact function/module boundary (a step
inside the `sync` CLI command's own flow vs. a new function in
`dispatch_store.py` it calls) is a plan-time detail; the CLI-visible
contract is that one `ghmem sync` invocation does both.

## Data flow, end to end

1. Item #N is triaged, routed to topic T. `RouteEvent` carries
   `item_updated_at` = #N's live `updated_at` at that moment.
2. Someone comments on #N upstream. The next `ghmem sync` refetches #N;
   its `updated_at` in the mirror moves forward.
3. That same `sync` invocation's detection pass notices #N's live
   `updated_at` now exceeds its `Route.item_updated_at`, and no newer
   `ReenterEvent` already covers it, so it appends one.
4. The reducer applies it: #N's `Dispatch` row is freed, topic T is
   dirtied immediately — before any new verdict exists.
5. `untriaged_heat_scores` now includes #N: its `Route` still exists but
   no longer counts as fresh, and nothing outstanding blocks a new
   `Dispatch`.
6. A later `serve_next` call serves #N again; `record_verdict` writes a
   fresh `RouteEvent` — same topic T or a different one, latest-wins as
   already supported — carrying a fresh `item_updated_at` stamp. T (or
   whichever topic #N lands in) is already dirty, so the next synthesis
   pass picks up #N's new content.

## Concurrency and edge cases

- **No-signal routes re-enter too.** A `Route` with verdict `NO_SIGNAL`
  is still a `Route`; the staleness check doesn't distinguish it from a
  real topic assignment. This is exactly the #1445/#3795/#3783 shape,
  now automatic. `ReenterEvent`'s topic-dirty step is a natural no-op
  here, since no topic is attached.
- **Dispatch race safety.** Re-reserving a `Dispatch` for a freed item
  goes through the same conditional-INSERT `reserve_dispatch` path
  already used for brand-new items — no new race surface, just a new
  caller of an existing one.
- **Repeated staleness after re-triage is expected, not a bug.** If #N
  goes stale again immediately (a comment lands right after the new
  verdict), the next sync correctly flags it again. That's an accepted
  cost of the "any `updated_at` change triggers" default — a wasted
  triage pass at worst, never corrupted state.

## What this does not touch

`record_verdict`'s B1 rejection of a live re-route stays exactly as it
is. It still fires for an item with a *fresh* `Route` — this design
never routes such an item to `serve_next` in the first place, so the
guard simply never gets exercised by this path. There is still no way
to re-triage an item on demand; the only way in is corpus-detected
staleness. No change to `heat`, `status`, `topic`, or `close`. Discussions
stay out of scope — the triage queue runs over `IssueOrPR` only, and
staleness detection only ever walks routed `IssueOrPR` rows.

## Testing

- **Reducer unit tests** for `"reenter"`: frees the `Dispatch` row,
  dirties the associated topic when one exists, no-ops the dirty for a
  no-signal route, no-ops the delete when no `Dispatch` row exists.
- **Staleness query tests** for `untriaged_heat_scores`: a fresh `Route`
  still excludes; a stale one doesn't; a pre-migration row (no
  `item_updated_at` in its source event) falls back to the rollout
  constant, tested on both sides of that date.
- **Idempotency test**: the sync detection pass, run twice against an
  item already covered by an unactioned `ReenterEvent`, appends nothing
  the second time.
- **Rebuild-equivalence test**: replay a ledger containing `route` →
  `reenter` → `route` (re-triage) through `rebuild_from_ledgers` and
  confirm it matches state built by applying the same events live.
- **End-to-end**: the full #N lifecycle from the data-flow section as
  one integration test.

Mutation targets:

- Query drops the `item_updated_at >= item.updated_at` clause (reverts
  to "any Route excludes") → a stale item never reappears in
  `untriaged_heat_scores`.
- Reducer's `"reenter"` branch skips the `Dispatch` delete → a stale
  item's `Route` shows fresh-excluded-by-dispatch forever, never served.
- Reducer's `"reenter"` branch dirties the topic unconditionally,
  including for `NO_SIGNAL` → a spurious dirty flag with no topic behind
  it.
- Rollout fallback reads the live rollout date instead of the fixed
  constant → the answer for a pre-migration row changes depending on
  when the query runs, rather than being pinned to rollout day.

## Open questions

- **Legacy `RouteData` parsing.** The compat path — stamping
  `ROLLOUT_CUTOFF` when `item_updated_at` is absent from a replayed
  `RouteData` — lives in `apply_routing_event`'s existing `"route"`
  branch, not in `"reenter"`; exact shape (a `.get()` default vs. a
  dedicated legacy-parse step) is a plan-time call.
- **`ROLLOUT_CUTOFF` placement and value.** A fixed constant, set to
  this feature's ship date; which module owns it (`ledger.py`, next to
  the other routing constants, is the natural home) is a plan-time
  detail.
- **Detection-pass wiring.** Whether it lives as a new function in
  `dispatch_store.py` called from `cli.sync`, or elsewhere — the
  CLI-visible contract (one `ghmem sync` call does fetch and detection)
  is what this spec commits to, not the internal seam.

## History

Written 2026-08-11, after the corpus's third and fourth manual
re-routes (#3795, #3783, alongside the earlier #1445) made the case that
a hand-run correction was no longer a one-off — see
`[[ghmem-ongoing-sync-not-snapshot]]` for the underlying argument that
triage judges content, not settledness, and that a routed item leaving
the pending pool forever is exactly the terminality this design removes.
The architecture was decided by working through the same live-vs-replay
question C2 raised for `close`, and finding it doesn't apply here:
staleness is a stateless read, not reducer state, so — unlike
`close` — no compare-and-set needs recording.
