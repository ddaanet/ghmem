# ghmem subproject 2 Part C2 — `ghmem topic` + `ghmem close`

Design spec. Assembles one topic's synthesis context behind a snapshot
token (`topic`), and records synthesis completion with a compare-and-set
that will not silently discard input that arrived mid-synthesis
(`close`). Second and final slice of Part C; the first, C1, landed the
`build_index` that a successful `close` regenerates `INDEX.md` through.

Parent spec: `2026-07-09-ghmem-design.md`, §Architecture (the `topic`,
`close` command bullets and the snapshot-token compare-and-set) and
§Agents (the synthesize agent's re-assemble-on-stale retry). C1 spec:
`2026-07-23-ghmem-c1-index-design.md`. This slice implements everything
Part C sketched but C1 deferred: the two commands, the `SnapshotEvent`
and `CloseEvent` routing-ledger members, and a new `Snapshot` table.

## What the two commands do

`ghmem topic <name>` reads the items triage routed to `<name>`, mints a
snapshot token, records it, and prints a synthesis context headed by
that token: an item index followed by the full thread text of every
routed item. The token binds this assembly to the topic's state at the
moment it was read.

`ghmem close <token>` answers `topic` the way `route` answers `triage`.
It recomputes the topic's state and compares it to what the token
recorded. If they match, synthesis is complete and current: the topic's
dirty flag clears and `INDEX.md` regenerates. If they differ — a route
or a comment landed between assembly and close — the dirty flag stays
set for the next run and nothing is discarded. A stale close is a normal
race, not an error.

## The central call — the compare-and-set is decided live, not on replay

The reducer that mirrors routing events into SQLite runs on two paths:
the live write, and a cold rebuild replaying `routing.jsonl` from
scratch. The architecture's load-bearing invariant is that the two
cannot diverge. That constraint decides where the compare-and-set lives.

`rebuild_from_ledgers` replays the fetch ledger in full **before** the
routing ledger, because a route references an item that must already
exist. So at the instant a `CloseEvent` is replayed, the corpus already
carries every item's *final* `updated_at` — not the value it held when
`close` ran live. A reducer that recomputed "is the snapshot still
current?" from live mirror state would therefore reach a different
verdict on replay than it did live, and the mirror would diverge from
the ledger that built it.

The resolution: **`close` computes the currency verdict live, and a
`CloseEvent` exists only when the verdict was clean.** A stale close
writes nothing (below), so the event's mere presence *is* the recorded
decision — the reducer's close branch clears the topic's dirty flag for
any `CloseEvent` it replays, and never recomputes currency. Replay
re-applies a recorded decision. Order-preserving replay handles the
rest: a route that lands after a clean close re-dirties the topic in
ledger order, exactly as it did live.

This makes "write only current outcomes to the committed ledger" the
load-bearing rule. A stale close that wrongly wrote a `CloseEvent` would
clear the topic on replay *after* the newer route that made it stale —
dropping that route's input. So the rule is enforced by test, not just
observed (see mutation targets).

This is the same reasoning that made `reserve_dispatch` a bespoke path
rather than a reducer branch — the live decision and the recorded one
must not be the same computation over different state.

## Snapshot currency — a content digest

A snapshot must go stale when either axis of the topic's synthesis input
changes: the *set of items* routed to it, or the *activity* on any of
those items. The parent spec names both ("its ledger-sequence /
`updated_at` high-water mark").

The snapshot records **one digest** over the sorted `(item_id,
updated_at)` pairs of the topic's routed items — a SHA-256 hexdigest of
a canonical serialization. A new route grows the set; a re-route away
shrinks it; a new comment bumps an item's `updated_at`. Every one of
those changes the digest. A route to a *different* topic does not, so
the check is topic-scoped and never forces a needless re-synthesis the
way a global ledger-sequence counter would.

Both commands derive the digest from the same query through the same
helper, so the assembly-time and close-time definitions cannot drift.
That single-source rule is the contract; where the helper physically
sits is a plan-time detail.

Two honest limits, recorded rather than engineered around:

- `updated_at` is GitHub's own activity proxy, and the parent spec picks
  it deliberately. A bare reaction that GitHub does not reflect in
  `updated_at` would not register as a change. The alternative — hashing
  each item's fully rendered thread — defeats the point of a cheap
  currency check, so the proxy stands and its gap is a Limitation.
- Routing **notes** are not in the digest. A note can only change via a
  re-route, and `record_verdict` rejects a second verdict for an
  already-routed item (the B1 policy), so under today's commands a
  routed item's note is immutable. If a later part wires a live
  re-route, the digest must grow to cover the note.

## Routing-ledger extension

`ledger.py` gains two `RoutingEvent` members. The plan **must** list
`ledger.py` in its file structure — the union, its `_ROUTING_BY_TYPE`
validation map, and the reducer all live across `ledger.py` and
`ingest.py`, and a plan that omits the ledger leaves the union
type-incomplete (the recurring plan-gap lesson).

```python
class SnapshotData(TypedDict):
    token: str
    topic: str
    digest: str

class SnapshotEvent(TypedDict):
    type: Literal["snapshot"]
    data: SnapshotData

class CloseData(TypedDict):
    token: str
    topic: str

class CloseEvent(TypedDict):
    type: Literal["close"]
    data: CloseData
```

`RoutingEvent` becomes `DispatchedEvent | RouteEvent | RequeueEvent |
SnapshotEvent | CloseEvent`, and both new tags join `_ROUTING_BY_TYPE`
so `validate_routing_event` covers them. The union's "grows in Part C"
docstring note is discharged and removed.

`CloseData` carries no `cleared` field: every `CloseEvent` means a clean
close, so the flag would always be true. It carries `topic` even though
the token resolves to it through the `Snapshot` row, so the reducer's
close branch clears the right topic without a cross-row lookup at replay
— matching how every other routing event carries the fields its branch
reads.

## `Snapshot` table

```python
class Snapshot(Base):
    token: Mapped[str] = mapped_column(primary_key=True)
    topic: Mapped[str]
    digest: Mapped[str]
```

The mirror's record of every minted snapshot: token to topic and the
digest captured at assembly. `close` reads it by token to recover the
topic and the digest it must compare against. No secondary index —
`close` looks up by the primary key and nothing queries by topic.

## Reducer branches

`apply_routing_event` grows two branches, both idempotent under the
rebuild-twice property:

- `"snapshot"` — `session.merge(Snapshot(...))`, a plain upsert by
  token. Assembling context completes no synthesis, so it touches no
  dirty flag.
- `"close"` — mark `Topic(data["topic"])` clean unconditionally (get the
  row, set `dirty = False`; a missing row is a no-op, as a routed topic
  always has one). No currency check here: the event exists only because
  the live close already found the snapshot current, so replay clears on
  its presence alone.

## `ghmem topic <name>`

### Output

```
snapshot: <token>

## Item index

- #2374 — softwrapcolumn option — open, opened 2023-01-04, last active
  2024-11-02, by <author> (maintainer) · 18 comments · 4 reactions ·
  7 participants · 3 related · note: maintainer asked for one option
  per axis, not a general knob
- #3107 — wrapindent PR — ...

## Threads

<show.render of #2374>

<show.render of #3107>
```

The item index is one line per routed item: number and title, state and
first/last-activity dates, author with standing annotation, the comment
/ reaction / participant / related-items counts, the diffstat for a PR,
and the routing note recorded at triage. The counts and title come
straight from `HeatScore` via `heat_scores_for`, which also imposes the
order — heat order, hottest first, the same order the threads then
follow. Everything else is a field read (`IssueOrPR`, the standing
table, `pr_diff_for` for the diffstat) plus the `Route.note`.

The threads section is `show.render` per item, unchanged, at its default
diff valve — the same rendering `ghmem show` produces, so the diff-body
cap (`DIFF_BODY_MAX_CHARS`, calibrated in subproject 4) applies here
too. Full thread text, diff included, is the parent spec's requirement;
the show-default valve is the safety valve against the vendor-dump tail,
not a synthesis-specific truncation.

Routed items are always issues or PRs — triage walks `IssueOrPR` heat
order and discussions are never triaged — so the item index needs no
discussion path.

### Assembly steps

1. Read the routed items for `<name>` (`Route.verdict == name`,
   constrained to corpus items, as `routes()` is).
2. If none, raise — an empty topic has nothing to synthesize (see edge
   cases).
3. Compute the digest over those items.
4. Mint a snapshot token (the session-token format: eight base62
   characters via `secrets.choice`; a distinct namespace from session
   tokens but the same shape and rationale).
5. Record the `SnapshotEvent` through the existing
   `Store.record_routing_event` — a plain upsert path, no compare-and-set.
6. Render and print the context.

## `ghmem close <token>`

Three outcomes, keyed on `serve`-style structure rather than exit codes
where the outcome is expected:

1. **Clean** — the recomputed digest matches the snapshot's. Write a
   `CloseEvent`, clear the topic's dirty flag, regenerate `INDEX.md` via
   C1's `build_index` + `render_index`, and print `closed <topic>` to
   stdout. Exit 0.
2. **Stale** — the digests differ. Write **nothing**: the dirty flag is
   already set and stays set, and appending a no-op event would only add
   ledger noise and a dead replay branch. Print `stale: <topic> changed
   since its snapshot; re-run ghmem topic` to stderr. Exit 0 — a race
   the synthesize agent retries, not a failure (mirroring triage's
   exit-0 terminal messages and `record_verdict` rejecting without
   touching the ledger).
3. **Unknown token** — no `Snapshot` row. Raise `UnknownSnapshotError`;
   the command prints it to stderr and exits 1. Bad input, like
   `TriageSessionError` — a token the system never minted.

The clean path's ledger append, dirty clear, and the digest read that
decided `cleared` all happen in **one Session/transaction** — a bespoke
`close_topic` path in `dispatch_store.py`, the compare-and-set sibling
of `reserve_dispatch`. `INDEX.md` regeneration is filesystem I/O over
`design_dir`, so the CLI does it after a clean `close_topic` returns,
the same seam `index` uses; the library function stays confined to the
routing store.

Re-closing an already-closed token recomputes and, if still current,
writes a second clean `CloseEvent` and re-clears an already-clear flag —
harmless and idempotent. Not worth a "already closed" guard.

## Concurrency

`close` never races a writer on its topic's rows. `route` (the triage
phase) and `close` (the synthesis phase) do not overlap in the
orchestration, and the synthesize agents run one topic per spawn, so two
closes never touch the same `Topic`, `Route`, or `Snapshot` row. A
single-Session read-decide-write is therefore sufficient without the
IMMEDIATE-transaction locking a genuine compare-and-set race would need.

This is a deliberate scoping call, not an oversight: the reserve-dispatch
race was real because the orchestrator spawns parallel triage sessions
by design, and this one is absent for the mirror-image reason. If a
later part interleaves routing and synthesis, revisit `close_topic` with
an IMMEDIATE transaction to close the read-then-write window.

## Module structure

New `topic.py`, mirroring `triage.py`: the library logic for both
commands and its tests, with `cli.topic` / `cli.close` as plumbing.

- `assemble(store, routing, name) -> str` — steps above; returns the
  printable context. Raises `TopicNotRoutedError` on an empty topic.
- `close_topic` lives in `dispatch_store.py` (it owns the routing
  tables' transactions) and returns a `CloseOutcome(topic, cleared)`;
  `topic.py` need not wrap it beyond the `Store` facade.

`store.py` grows the facade methods `routed_items(name)`,
`topic_digest(name)`, and `close_topic(routing, token)`, delegating to
`dispatch_store.py` — the pattern every triage-state method already
follows. The digest helper is shared between `topic_digest` and
`close_topic` so the definition has one home.

`exceptions.py` gains `TopicNotRoutedError` (empty topic at assembly)
and `UnknownSnapshotError` (unminted token at close), both `ValueError`
like `TriageSessionError` and `UnknownRoutingEventError`, each carrying
its structured value (`name`, `token`).

`cli.py` gains two `@main.command`s. `topic` opens the store, calls
`assemble`, prints, and maps `TopicNotRoutedError` to a stderr line and
exit 1. `close` calls `Store.close_topic`, maps `UnknownSnapshotError`
to exit 1, then branches on `cleared`: regenerate `INDEX.md` and print
`closed …` on stdout, or print the stale line on stderr.

## Edge cases

- **`topic` on a name with no routed corpus items** — raise
  `TopicNotRoutedError`, exit 1. A dirty topic always has routed items,
  so this only fires on a manual mistake (a misspelled name, or a topic
  every routed item of which was never fetched).
- **`close` on a token whose topic was re-routed after assembly** —
  the digest differs, so the close is stale. Correct: the newer route
  already re-dirtied the topic, and clean-closing here would drop it.
- **`close` twice on the same token** — idempotent (see above).
- **A routed item deleted upstream or never fetched** — excluded from
  the routed-items query by the corpus constraint, exactly as `routes()`
  handles it, so it contributes to neither the digest nor the render.

## Testing

`topic.py` tests at the library level, per the codebase's seam.

- `assemble` — over a small fixture store with items routed to a topic:
  the header carries the minted token, the item index carries each
  routed item's counts and its note in heat order, the threads section
  carries each item's `show.render`, and a `SnapshotEvent` with the
  right digest landed in the ledger.
- `close_topic` — the three outcomes: a clean close clears dirty and
  appends one `CloseEvent`; a close after a mutated routed item (bump
  one item's `updated_at`, or route a fresh item to the topic) is stale,
  leaves dirty set, and appends nothing; an unminted token raises
  `UnknownSnapshotError`.
- The live-vs-rebuild property, the architecture's central claim:
  replaying the ledger a clean close just wrote reproduces the same
  cleared dirty flag (extends the existing `test_routing_live` pattern).

Mutation targets the plan must name, each with the test that must die:

- Digest drops `updated_at` (item ids only) → a new comment on a routed
  item no longer stales the close.
- Digest drops membership (updated_at only, or a fixed set) → a fresh
  route to the topic no longer stales the close.
- `close_topic` appends the `CloseEvent` on the stale path too → replay
  clears the topic *after* the newer route that made it stale, dropping
  that route's input. This is the invariant the "write only current
  outcomes" rule protects.
- Reducer close branch no-ops → a clean close never clears dirty and the
  topic re-synthesizes forever.
- Item index omits the routing note → the note is missing from a routed
  item's line.
- `close_topic` compares the current digest against itself (reads it
  where it should read the snapshot's) → every close reports clean.

The fixture must give the topic at least two routed items with
*distinct* `updated_at` values and heat scores, so the heat ordering and
the per-item digest contribution are both discriminable — the recurring
"a fixture too uniform to bite" trap.

## What C2 does not touch

No change to triage, `route`, `heat`, `status`, or `sync`. No live
re-route (still the B1 rejection). The 44 unsurfaced `fetch_failed`
records remain a separate `status` change, not this slice. Live-run
findings 6 (resume re-parse), 8 and 9 (diff-fence marker, batch-budget
calibration) are subproject-4 or their own follow-ups, untouched here.
Finding 3 (first-page truncation clipping `related_items`) is settled by
this design only in that `topic` inherits whatever `heat` and `show`
already do with a clipped item; it is not re-opened.

## Open questions

- **Digest helper placement.** It must be reachable by both
  `topic.py`'s assembly and `dispatch_store.py`'s `close_topic` without
  an import cycle (`topic.py → store.py → dispatch_store.py`). A pure
  function in `dispatch_store.py`, imported by `store.topic_digest` and
  called inside `close_topic`'s session, avoids the cycle; confirm the
  exact shape at plan time from the call sites.
- **Item-index line format.** The sketch above is illustrative. The
  precise punctuation is a plan-time rendering choice; the contract is
  the field set (number, title, state, dates, author+standing, the four
  counts, diffstat for PRs, note), not the separators.

## History

Carved from Part C during the 2026-07-24 design pass, after C1 landed
`build_index` and the first live-data run gave Part C its evidence base.
The compare-and-set's placement (decided live, recorded in the event)
was forced by the fetch-before-routing replay order rather than chosen;
the currency representation (a topic-scoped content digest over
`(item_id, updated_at)`) was chosen over a literal ledger-sequence /
high-water-mark pair for being single-valued, topic-scoped, and
precise on both axes at once.
