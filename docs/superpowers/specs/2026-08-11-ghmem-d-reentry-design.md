# ghmem dispatch Part D — re-entry on corpus change

Design spec. Lets a routed item whose GitHub thread changes — a new
comment, a state flip — return to the triage pending pool automatically,
so the next sync-triage-synthesize cycle re-judges it and refreshes its
topic doc. Replaces the unenforceable "revisit if the PR lands" notes
triage was writing by hand for exactly this case. It does not touch the
separate, still-manual tool for correcting a verdict that was wrong on
content that never changed — see Motivation.

Parent spec: `docs/ghmem-design.md` §Architecture (the `route`
reducer branch, latest-wins `Route`, "re-routing marks both old and new
topics dirty") and §Error handling. B1 policy reference:
`2026-07-21-ghmem-dispatch-triage.md` (`record_verdict` rejects a
live re-route as `already_routed` — deliberate, and explicitly scoped as
"out of scope until a later part asks for it"). This is that part — for
a *stale* `Route` only; a live re-route stays rejected (see "What this
does not touch").

## Motivation

Triage had started writing "revisit if the PR lands" into routing notes
for a verdict that was only provisionally settled. That note is
unenforceable: nothing reads it back, nothing schedules the revisit, and
it decays into a note nobody acts on. Re-entry replaces the note with a
mechanism: the corpus, not a hand-written reminder, is what decides an
item needs a second look.

A separate tool already exists for correcting an already-routed item,
and predates this design: hand-append a corrected `route` event straight
to the ledger (bypassing `record_verdict`'s guard, which only fences the
session-loop path) and run `rebuild` — used for #1445
(`docs/ghmem-tail-sample.md` found the original `no-signal` call wrong)
and for #3795/#3783. Those were calibration catches, not corpus-change
catches: a human re-read content that had not changed and decided the
earlier verdict was wrong. Re-entry doesn't replace that tool or that
judgment call — it solves a different problem, a verdict going stale
because the *thread* moved, not because triage misjudged it the first
time. This design's own rollout could not reuse that tool — see
Rollout for why, and for what migrated the corpus instead.

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
not nullable from the start — see Rollout below for how the existing
63 routed rows were given a value at migration time.

## Rollout — the corpus was migrated by editing ledger lines, not appending events

`corpus/routing.jsonl` carried 63 pre-existing `route` lines when
`item_updated_at` was added to `RouteData` — more lines than routed
items, since a re-routed item leaves more than one `route` line behind
and replay validates every line, not just the latest one per item. Each
of the 63 was edited in place, gaining `item_updated_at` stamped from
its item's `updated_at` in the fetch mirror at migration time — the
same value an event-based backfill would have produced, written as an
edit to the existing line rather than a new one appended after it.
`session`, `verdict`, `note` and `item_id` were left untouched on every
line; only the new field was added.

An event-based backfill — append one corrected `route` event per
currently-routed item, then `rebuild` — cannot work, at any point in
the rollout ordering. Producing the events needs `store.routes()`,
which needs the `item_updated_at` column already live in the mirror;
getting that column live means a `rebuild`, and `rebuild` replays every
line of `routing.jsonl` through `validate_routing_event` before it
replays anything else — so the first legacy `route` line, still
missing `item_updated_at`, raises `msgspec.ValidationError` before a
single backfill event is ever produced, let alone appended. Appending
the corrected events afterward doesn't rescue this either: a full
sequential replay validates *every* line in order, so a corrected event
appended after an un-migrated original for the same item is never
reached — the original line's `ValidationError` fires first, every
time. This section's original claim — "there's no pre-migration row
left for the reducer to special-case" — is true of the derived mirror
row, which is last-write-wins and only ever shows the newest line per
item, but false of the ledger line itself, which is what replay
actually validates, one line at a time.

The edit needed no hand-verification, the same character as the
original #1445/#3795/#3783 corrections but for the opposite reason:
those corrected a verdict a human had judged wrong; this corrected
nothing about any verdict — the edit was purely mechanical, adding one
field whose value was read straight off the fetch mirror.
`Route.item_updated_at` is then `NOT NULL` from the column's first
day, same as originally intended — the migration just reached that
state by editing the ledger's existing lines instead of appending new
ones after them.

It ran once, by hand, via a one-off script that was never added to the
repository: a transition that happens exactly once, for a tool with a
user base of one, doesn't earn a maintained module any more than it
earns a maintained CLI subcommand. There is nothing left to run again.

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

`ghmem sync` never touches the SQLite mirror — it only appends to the
fetch ledger, so a comment fetched mid-sync doesn't move
`IssueOrPR.updated_at` in the mirror until the next rebuild. `ghmem
rebuild` is what replays both ledgers into a fresh mirror, so it's the
point where a live `updated_at` and a stale `Route.item_updated_at` are
both in hand at once — the natural home for the walk, not an extra
mirror-writing step bolted onto `sync`, which has no such path today.

`cli.rebuild` gains a step, after `rebuild_from_ledgers` returns, that
walks every item currently carrying a `Route` — a new
`dispatch_store.stale_routes(engine)`, alongside the existing
`routes()`/`dispatched_unrouted()` queries — and appends a
`ReenterEvent` for any whose live `updated_at` has moved past its stamp
and that doesn't already have a newer, still-unactioned `ReenterEvent`
— the idempotency guard that keeps a repeatedly-stale item from being
flagged again every rebuild before it's re-triaged. Each event goes
through `store.record_routing_event`, the same incremental
mirror-update path `cli.requeue` already uses to apply its events, so no
second full rebuild is needed to see the effect.

This still means no new CLI command in the operator's cadence — the
intent behind the earlier "fold into an existing command, not a
separate one" call — just `rebuild` rather than `sync`: `sync` has no
mirror-writing path today, and the operator's normal cadence already
runs `rebuild` right after `sync` regardless.

## Data flow, end to end

1. Item #N is triaged, routed to topic T. `RouteEvent` carries
   `item_updated_at` = #N's live `updated_at` at that moment.
2. Someone comments on #N upstream. The next `ghmem sync` refetches #N
   into the fetch ledger; the next `ghmem rebuild` replays it, and #N's
   `updated_at` in the mirror moves forward.
3. That same `rebuild`'s detection pass notices #N's live `updated_at`
   now exceeds its `Route.item_updated_at`, and no newer `ReenterEvent`
   already covers it, so it appends one.
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
  real topic assignment — new activity on a no-signal item's thread
  re-enters it just like a topic-routed one. `ReenterEvent`'s
  topic-dirty step is a natural no-op here, since no topic is attached.
  This still doesn't catch #1445's class of error — a no-signal call
  wrong on content that never changed — only genuine new activity on a
  no-signal item.
- **Dispatch race safety.** Re-reserving a `Dispatch` for a freed item
  goes through the same conditional-INSERT `reserve_dispatch` path
  already used for brand-new items — no new race surface, just a new
  caller of an existing one.
- **Repeated staleness after re-triage is expected, not a bug.** If #N
  goes stale again immediately (a comment lands right after the new
  verdict), the next rebuild correctly flags it again. That's an
  accepted cost of the "any `updated_at` change triggers" default — a
  wasted triage pass at worst, never corrupted state.

## What this does not touch

`record_verdict`'s B1 guard was narrowed, not left alone: it now
rejects a second verdict only when the existing `Route` is *fresh*
(its `item_updated_at` still matches or leads the item's live
`updated_at`). A reentered item's `Route` is stale by construction —
that staleness is the whole point of re-entry — so it clears the
guard and the fresh verdict from `serve_next` goes through; a live
re-route attempt still carries a fresh `Route` and is rejected exactly
as before. This path is exercised on every reenter, not skipped — it
is what step 6 of "Data flow, end to end" above depends on. There is
still no way to re-triage an item on demand; the only way in is
corpus-detected staleness. No change to `heat`, `status`, `topic`, or
`close`. Discussions stay out of scope — the triage queue runs over
`IssueOrPR` only, and staleness detection only ever walks routed
`IssueOrPR` rows.

## Testing

- **Reducer unit tests** for `"reenter"`: frees the `Dispatch` row,
  dirties the associated topic when one exists, no-ops the dirty for a
  no-signal route, no-ops the delete when no `Dispatch` row exists.
- **Staleness query tests** for `untriaged_heat_scores`: a fresh `Route`
  still excludes; a stale one doesn't.
- **Idempotency test**: the detection pass, run via two successive
  `rebuild` calls against an item already covered by an unactioned
  `ReenterEvent`, appends nothing the second time.
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
- Backfill script skips an item in its walk → that row is left with no
  `item_updated_at`, which the column's `NOT NULL` constraint catches
  loudly at write time rather than silently at query time.

## History

Written 2026-08-11, prompted by triage's recurring unenforceable
"revisit if the PR lands" notes — see
`[[ghmem-ongoing-sync-not-snapshot]]` for the underlying argument that
triage judges content, not settledness, and that a routed item leaving
the pending pool forever is exactly the terminality this design removes.
The architecture was decided by working through the same live-vs-replay
question C2 raised for `close`, and finding it doesn't apply here:
staleness is a stateless read, not reducer state, so — unlike
`close` — no compare-and-set needs recording.

Corrected 2026-08-12: "What this does not touch" originally predicted
B1 would stay unchanged and never fire on this path. Task 5's
implementation found that prediction false — before the fix,
`record_verdict` rejected on any existing `Route`, so the reenter path
itself tripped B1 the first time it actually ran end to end. The fix
narrows the guard to fire only on a *fresh* `Route`, reusing the same
freshness predicate `untriaged_heat_scores` and `stale_routes` already
use; the section above now states that as the current behavior.

Corrected 2026-08-12: Rollout originally described an append-based
backfill procedure — append one corrected `route` event per
currently-routed item, then `rebuild`. The final whole-branch review
proved that procedure cannot execute against any pre-migration corpus,
at any point in the rollout ordering: `rebuild` validates every
existing ledger line, so the first un-migrated legacy `route` line
raises `msgspec.ValidationError` before a backfill event is ever
produced or, appended afterward, ever reached. The section above now
describes what actually migrated the corpus: an in-place edit of the
63 pre-existing `route` lines in `corpus/routing.jsonl`, each gaining
the stamped field the append-based procedure would have carried.
