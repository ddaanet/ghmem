# Outline — `route` deletes a topic it empties

## Scope

`unroute` deletes a `Topic` row its withdrawal leaves empty; `route`
does not, so re-routing a topic's last item leaves an empty dirty row
that `status` offers for synthesis and `topic.assemble` then refuses
with `TopicNotRoutedError`. Converge `route` on `unroute`: one rule,
one code path, `Topic` exists exactly while some `Route` carries its
name as a verdict other than `no-signal`.

Three behaviours follow from that rule, only the first of which the
one-line framing suggests:

- **Re-routing** a topic's last item drops the row it leaves.
- **Routing to `no-signal`** from a topic's last item drops it too.
  The change is not confined to movement between topics.
- **Re-routing to the same topic** — reached through re-entry, not a
  live correction — dirties the row rather than dropping it.

Settles the first Open question in `docs/ghmem-design.md`.

## Mechanism

`_apply_route_event` calls `_release_topic` for the topic it leaves
instead of `_mark_topic_dirty`. Two ordering constraints, both
load-bearing and both invisible in the one-line framing:

- **The release runs after `session.merge`.** `_release_topic` counts
  the routes still carrying the name and relies on autoflush emitting
  the pending write before that query. Called before the merge, the
  item being re-routed still carries the old verdict, the count is
  never zero, and the row is never dropped.
- **`left` is captured before the merge.** `session.get(Route, id)`
  returns the identity-mapped instance that `merge` then updates in
  place, so reading `previous.verdict` after the merge yields the *new*
  verdict. The current code already reads it before the merge, so this
  is a preservation requirement rather than a new one.

The first constraint is also what makes the change concurrency-safe,
which is the stronger reason and the one worth writing down. The
autoflush that emits the pending `UPDATE` is where SQLite's writer
lock is taken, so the `COUNT` and the `DELETE` land on the same side
of it: a worker routing another item *into* the same topic cannot
commit between them. Whichever order the two workers reach the lock
in, the result is right — the count sees the sibling and dirties, or
the topic is dropped and the sibling's route re-mints it. Calling
`_release_topic` before the merge would put the count in an unlocked
read and open exactly the check-then-act window `reserve_dispatch`
exists to avoid.

### The mint race the change would otherwise widen

`_mark_topic_dirty` is a bare get-then-add — the pattern
`reserve_dispatch`'s docstring rules out, because the `sqlite3` driver
opens a transaction before a write but not before a read. Two workers
routing two different items into the same not-yet-existing topic both
read `None`, both queue the `INSERT`, and the loser fails the primary
key on flush. Since the reducer runs before the ledger append, the
loser's verdict never lands at all: it dies with an opaque
`IntegrityError` rather than a `TriageSessionError`.

Today that window closes permanently once a topic exists. After this
change a topic can be emptied and dropped, so the window reopens every
time — which is why the fix belongs in this change rather than after
it. `_mark_topic_dirty` becomes a conditional upsert, mirroring
`reserve_dispatch`:

```python
sqlite_insert(Topic).values(name=name, dirty=True).on_conflict_do_update(
    index_elements=["name"], set_={"dirty": True}
)
```

It must go through `session.execute()` on the reducer's own session.
`ingest.rebuild_from_ledgers` replays the *entire* routing ledger in a
single `Session`, so a topic emptied at one event and re-minted at a
later one is a within-session delete-then-recreate: the pending
`DELETE` has to flush before the recreate or it wipes the new row.
`session.execute()` autoflushes, which makes the upsert safe there —
and more robustly so than the present `session.get`/`session.add`,
which is safe only because an unrelated autoflush happens to
intervene.

Verified by a probe (applied, run, reverted): with the call after the
merge and `left` captured before it, exactly 5 tests go red and 273
pass, which is the whole suite.

## Per-file changes

### `src/ghmem/routing_reduce.py`

- `_apply_route_event` — capture `left` before the merge; after the
  merge, `_release_topic(session, left)` when `left` is neither `None`
  nor `NO_SIGNAL`. Its summary line becomes `Replace an item's
  verdict, releasing the topic it leaves.` — 62 characters with
  indent, inside the docformatter dead zone (`memory/ghmem-project.md`:
  a summary of 81–88 characters passes ruff, gets reflowed, then trips
  D205). It is the only summary line that changes; `_release_topic`'s
  and `_mark_topic_dirty`'s stay accurate as written.
- `_release_topic` — delete the "Scoped to `"unroute"`" paragraph and
  the open-question sentence; state the rule as covering withdrawal
  and re-route alike. Reword the autoflush comment: the caller's
  pending write is a `delete` for `unroute` and an `update` for
  `route`. Add the concurrency sentence above.
- `_mark_topic_dirty` — becomes the upsert, with the
  `sqlalchemy.dialects.sqlite.insert` import that implies. Its
  docstring says why it is an upsert and not a get-or-create by
  pointing at `reserve_dispatch`'s reasoning rather than restating it.
- The paragraph on `Snapshot` rows survives unchanged; its argument
  carries over to `route` as written.
- The paragraph on `docs/design/<name>.md` needs its last clause
  softened, not carried over. "A synthesized doc also remains true
  prose about items that really were routed there" holds under
  `unroute`, where the verdict is simply withdrawn. Under re-route the
  item carries a *live* verdict naming a different topic, so the
  orphaned doc is contradicted by current state rather than merely
  unaccompanied by it. The conclusion survives on the
  replay-destructiveness argument, which is the load-bearing one.

### `tests/` — the red set, re-derived by probe

Five, matching the design doc's count. Membership did not match its
framing. Every `:N` below is the assertion that fails, not the `def`.

- `test_routing_replay.py::test_route_creates_topic_marked_dirty`
  (`:57`) — **not a re-route test.** It reds because the shared replay
  fixture ledger later re-routes item #1 away from `softwrap`,
  emptying it. Fixture coupling, not a contract change. Retarget the
  assertion to `wrapping` (see the settled decision below) and rewrite
  the docstring, which names "softwrap" in both its `When:` and its
  `Then:`.
- `test_routing_replay.py::test_reroute_replaces_the_verdict_and_dirties_both_topics`
  (`:107`) — assert the left topic is gone; rename accordingly. Its
  docstring argues the old-topic half "cannot be proven from a
  rebuild… so the assertion below is deliberately the weaker 'both are
  dirty', not evidence of why." After this change a rebuild *can*
  distinguish, so **delete that paragraph** rather than rewording it:
  the change removes the limitation it documents and upgrades the test
  to real evidence.
- `test_routing_replay.py::test_rebuild_is_idempotent` (`:339`) —
  expected set becomes `{"wrapping"}`. Idempotence itself still holds
  (probe-confirmed).
- `test_routing_live.py::test_live_writes_match_a_cold_rebuild`
  (`:158`) — expected set becomes `{"wrapping"}`. `live == rebuilt`
  still passes (probe-confirmed); only the hardcoded set was stale.
- `test_routing_live.py::test_reroute_dirties_the_topic_it_leaves`
  (`:185`) — purpose inverts. Split into the emptied case (row
  dropped) and the surviving-sibling case (row kept, dirty). Its
  docstring carries a second mutation note — "dropping the reducer's
  old-topic dirtying changes nothing observable" (`:163-166`) — as
  falsified as the replay one, and needing the same rewrite.

**Compensate the shrunk topic sets.** `memory/ddaanet/green-is-not-evidence.md`
names three of these five by shape, and was written from this file:
`test_reroute_replaces_the_verdict_and_dirties_both_topics` is its
"satisfied by birth state", `test_live_writes_match_a_cold_rebuild` its
"two derived states compared", `test_rebuild_is_idempotent` its
"idempotence standing in for teardown". In the latter two the
hardcoded topic set *is* the guard keeping them from passing
vacuously, and this change shrinks both from two elements to one. Add
an explicit `assert session.get(Topic, "softwrap") is None` to each,
which turns the loss into positive evidence for the drop rather than
absence of contradiction.

### `tests/` — new coverage

The surviving-sibling case is the second half of the
`test_reroute_dirties_the_topic_it_leaves` split above, not a
standalone addition. Two items routed to `softwrap`, `softwrap` marked
clean, one re-routed to `wrapping`, then assert `softwrap` survives
and is dirty again.

Two things to get right about it:

- **It is a regression guard, not evidence for this change.**
  `_release_topic`'s `remaining → _mark_topic_dirty` branch is already
  covered — `test_unroute.py:107`,
  `test_unroute_dirties_a_topic_that_keeps_items`, is structurally the
  same test with `unroute` as its final event. What is uncovered is
  narrower: that *`route`* reaches that branch. And under the mutation
  that actually isolates this change — reverting `_apply_route_event`
  to call `_mark_topic_dirty` — the new test stays green, because on
  the surviving-sibling path old and new code are observationally
  identical. Mutating `_release_topic` to delete unconditionally reds
  the existing unroute test too, so it discriminates nothing. Name the
  test what it is rather than prescribing a validation that cannot
  confer the evidence it appears to.
- **Reuse the birth-state note** already at `test_unroute.py:109-111`:
  "a Topic row is born dirty, so asserting `dirty is True` against a
  fresh row would pass with the dirtying step removed."

Three further tests follow from the mechanism section:

- `route → no-signal` emptying a topic drops it.
- Same-topic re-route via re-entry dirties rather than drops — the
  common re-entry outcome, and where the count-after-write ordering is
  load-bearing in the opposite direction.
- A topic emptied and re-minted within one replay session, which is
  the only deterministic test of the upsert. The cross-process race
  itself is closed by construction and is not testable without
  threads; record that rather than attempting it.

**Placement is an open runbook question.** `test_unroute.py` has
`_dispatch`, `_route`, `_live_store` and `_session` helpers that make
these cheap; `test_routing_live.py`, where `route` tests belong, has
only module-level event constants and no multi-item builder. Either
duplicate the helpers or add a parameterized builder — decide in the
runbook, not during execution.

## Docs

- `docs/ghmem-design.md` — drop the first Open question (`:250-263`,
  its closing "Rationale as it stands" line included). Its dated
  observation about zero empty `Topic` rows goes with it, correctly:
  situational state does not belong in a design doc
  (`memory/ddaanet/design-doc-no-situational-state.md`).
- `docs/ghmem-design.md:88-90` — **FR7's Rationale line cites the
  decision by title.** Renaming the decision breaks this
  cross-reference; it is the only one outside the dropped bullet.
  Decide at the same time whether FR7's own prose gains a
  topic-lifecycle clause: it describes re-routing as "supersedes one
  with a better verdict" and says nothing about a `Topic` row, which
  after this change re-routing can delete.
- `docs/ghmem-decisions.md:120-133` — the decision generalizes from
  "Unrouting a topic's last item deletes the `Topic` row" to any event
  that empties one. Its closing clause about `docs/design/<name>.md`
  needs the same softening as the reducer docstring above; they are
  one edit stated twice. The accepted limitation below belongs here,
  as the cost the decision knowingly takes on.
- `docs/ghmem-decisions.md` — a **separate** entry for the upsert. It
  closes a pre-existing cross-process race and is design-significant
  on its own; folding it into the emptied-topic entry buries it.
- `docs/ghmem-rejected.md` — the "against converging" argument moves
  into the `## Rejected alternatives` list, which takes bold-titled
  entries. Give it one: **"Leaving a re-routed topic's emptied row
  dirty."** It was the live rationale and is now overruled.
- `docs/ghmem-changelog.md` — a reversed decision is
  design-significant. The file's convention is
  `**YYYY-MM-DD — <title>.**`, a narrative of what changed and why,
  closing with a pointer to the frozen record — which means this plan
  directory must end up carrying one.

## Accepted limitation

Under the current behaviour an emptied-by-re-route topic stayed dirty,
so a synthesis worker had a chance to rewrite its doc. After this
change nothing will: `docs/design/<name>.md` is left on disk, `ghmem
index` keeps listing it, and no dirty row carries a rewrite.

This is the cost the "against converging" argument named, accepted
deliberately. It is **not** identical to the cost `unroute` already
carries, and the outline should not claim so:

- `corpus/routing.jsonl` holds 0 `unroute` events against 103 routes.
  The precedent has never actually been paid.
- Under `unroute` the item returns to the queue carrying no verdict,
  so the doc is merely unaccompanied, and a later re-triage may route
  the item back and re-mint the topic. Under re-route the item carries
  a live verdict naming a different topic, so the doc is contradicted
  by current state. Same shape, strictly worse.

Unrealized today — the mirror holds 0 empty `Topic` rows — but not
durably so: 57 of 71 topics are singletons, so a re-route away from a
synthesized singleton is the likely case rather than the exotic one.

No surface will report it. `build_status` reads only the store and the
fetch ledger and never touches the filesystem, so a detector there
would be a new dependency, not a one-line addition. Record it as a
limitation, not as a defect to guard against.

## Follow-up, out of scope

An **orphan** is a `docs/design/<name>.md` on disk with no `Topic` row
of that name: `ghmem index` lists it, `ghmem topic <name>` refuses it
with `TopicNotRoutedError`, and nothing schedules its rewrite or
removal.

Nothing needs to be stored to find them. The durable record of a
synthesized doc is the `Snapshot` row — `(token, topic, digest)`,
written when a synthesis context is minted and never deleted, since
`_apply_close_event` only clears `Topic.dirty`. The live mirror holds
6 snapshot rows over exactly the 5 topics with docs on disk. So
orphans are `set(Snapshot.topic) - set(Topic.name)` — or more simply
`{docs/design/*.md} - {Topic.name}`, a directory listing minus a
table, which also catches a doc no `snapshot` event produced.

Recorded as a named follow-up: **an orphan sweep over
`{docs/design/*.md} - {Topic.name}`, in `index` or a new `gc`.** Not
built here. Preserving an empty dirty `Topic` as the marker instead
was considered and rejected: `dirty` means "needs re-synthesis" and is
what `status` dispatches synthesis workers from, so an empty dirty row
is precisely the `TopicNotRoutedError` breakage this change exists to
remove. Distinguishing "synthesize me" from "delete me" needs a third
state, which is a larger design than this one.

## Settled decision

`test_route_creates_topic_marked_dirty` needs a topic the fixture does
not later empty. **Retarget its assertion to `wrapping`.**

The alternative — a second item routed to `softwrap` — is not merely
costlier, it is not viable as written. `test_dispatch_store.py:290`
asserts `dispatched_unrouted() == [I_3]`, and I_3 is the fixture's
only dispatched-but-unrouted item; routing it to `softwrap` breaks
that test outright. A third topic would need a new I_4 in **both**
`routing_events.jsonl` and `routing_fetch.jsonl`, which holds only
I_1–I_3. The fixture is read by three test files, not one.

The default is also a one-line change: the fixture's re-route event
carries the *same* `item_updated_at` as the original route, so the
test's second assertion —
`route.item_updated_at.isoformat() == "2026-01-01T00:00:00+00:00"` —
survives untouched.

Accepted consequence: after retargeting, the fixture's only surviving
topic is minted by a re-route, so no replay-level test covers a fresh
first mint. Live coverage remains, implicitly, through
`test_reroute_dirties_the_topic_it_leaves`.

## Boundaries

**IN:** the `route` branch of the routing reducer, the shared
`_mark_topic_dirty`, the five red tests, the tests listed above, four
docs.

**OUT:**

- `unroute`, `reenter`, `requeue`, `close` — **behaviour** unchanged.
  Their code is not: `_apply_reenter_event` calls `_mark_topic_dirty`
  directly and `_apply_unroute_event` reaches it through
  `_release_topic`, so the upsert changes what runs under them and
  their tests must be re-run.
- Filesystem deletion of a synthesized doc. The reducer is replayed
  wholesale by `rebuild_from_ledgers`; a filesystem delete inside it
  would make a rebuild destructive.
- The orphan sweep above.
- `Snapshot` rows — untouched, and load-bearing for that follow-up as
  the durable record of a synthesized doc.
- No ledger schema change. That is not the same as no rebuild: the
  mirror is derived under the old reducer, so bringing it into line
  means a `ghmem rebuild`. Verified a no-op on 2026-09-07 — 0 empty
  `Topic` rows, 71 topics against 71 distinct non-`no-signal`
  verdicts — but that is a fact about the corpus, not a structural
  guarantee. Re-run at landing rather than inheriting this reading:

  ```sql
  select t.name from topic t
  where not exists (select 1 from route r where r.verdict = t.name)
  ```

**Widened deliberately:** `sqlite_insert` puts a dialect-specific
import in `routing_reduce.py`, which is currently dialect-neutral.
Consistent with `dispatch_store.py`, and the mirror is SQLite-only.

## Phase typing for `/runbook`

- **tdd** — the reducer change (including the upsert) plus the updated
  and new tests. The runbook enumerates them; this outline
  deliberately states no count, since the arithmetic has already
  drifted once.
- **gate** — `just precommit`, as an explicit item. The tdd phase
  touches `src/` and `tests/`, which are the gate's cache inputs.
- **inline** — the doc edits. They sit outside the gate's cache
  inputs, so they do not re-trigger it.
