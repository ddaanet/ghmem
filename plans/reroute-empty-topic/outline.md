# Outline — `route` deletes a topic it empties

## Scope

`unroute` deletes a `Topic` row its withdrawal leaves empty; `route`
does not, so re-routing a topic's last item leaves an empty dirty row
that `status` offers for synthesis and `topic.assemble` then refuses
with `TopicNotRoutedError`. Converge `route` on `unroute`: one rule,
one code path, `Topic` exists exactly while some `Route` carries its
name.

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
  verdict.

Verified by a probe (applied, run, reverted): with the call after the
merge and `left` captured before it, exactly 5 tests go red and 273
pass.

## Per-file changes

### `src/ghmem/routing_reduce.py`

- `_apply_route_event` — capture `left` before the merge; after the
  merge, `_release_topic(session, left)` when `left` is neither `None`
  nor `NO_SIGNAL`. Rewrite the summary line: the topic it leaves is now
  dirtied *or dropped*, not dirtied.
- `_release_topic` — delete the "Scoped to `"unroute"`" paragraph and
  the open-question sentence; state the rule as covering withdrawal and
  re-route alike. Reword the autoflush comment: the caller's pending
  write is a `delete` for `unroute` and an `update` for `route`.
- The paragraphs on `docs/design/<name>.md` and on `Snapshot` rows
  survive unchanged — both arguments carry over to `route` as written.

### `tests/` — the red set, re-derived by probe

Five, matching the design doc's count. Membership did not match its
framing:

- `test_routing_replay.py::test_route_creates_topic_marked_dirty`
  (`:57`) — **not a re-route test.** It reds because the shared replay
  fixture ledger later re-routes item #1 away from `softwrap`,
  emptying it. Fixture coupling, not a contract change.
- `test_routing_replay.py::test_reroute_replaces_the_verdict_and_dirties_both_topics`
  (`:107`) — assert the left topic is gone; rename accordingly. Its
  docstring's mutation note ("deleting the reducer's old-topic dirtying
  keeps this test green") stops being true and must be rewritten, not
  kept.
- `test_routing_replay.py::test_rebuild_is_idempotent` (`:339`) —
  expected set becomes `{"wrapping"}`. Idempotence itself still holds
  (probe-confirmed).
- `test_routing_live.py::test_live_writes_match_a_cold_rebuild`
  (`:158`) — expected set becomes `{"wrapping"}`. `live == rebuilt`
  still passes (probe-confirmed); only the hardcoded set was stale.
- `test_routing_live.py::test_reroute_dirties_the_topic_it_leaves`
  (`:185`) — purpose inverts. Split into the emptied case (row
  dropped) and the surviving-sibling case (row kept, dirty).

**New coverage, not a rewrite.** No current fixture re-routes an item
away from a topic that still holds another route, so the *dirtying*
half of the branch has no test. Add one: two items routed to
`softwrap`, `softwrap` marked clean, one item re-routed to `wrapping`,
then assert `softwrap` survives and is dirty again.

That test is green against unchanged code, so it is not evidence on its
first run. Validate it against a wrong implementation — mutate
`_release_topic` to delete unconditionally and confirm it reds — rather
than accepting the first green.

### Docs

- `docs/ghmem-design.md` — drop the first Open question.
- `docs/ghmem-decisions.md:120-129` — the decision generalizes from
  "Unrouting a topic's last item deletes the `Topic` row" to any event
  that empties one. The "against converging" argument moves to rejected
  alternatives; it was the live rationale and is now overruled.
- `docs/ghmem-changelog.md` — a reversed decision is design-significant.

## Accepted limitation

Under the current behaviour an emptied-by-re-route topic stayed dirty,
so a synthesis worker had a chance to rewrite its doc. After this
change nothing will: `docs/design/<name>.md` is left on disk, `ghmem
index` keeps listing it, and no dirty row carries a rewrite. This is
the cost the "against converging" argument named, accepted deliberately
and identical to the cost `unroute` already carries. Record it as a
limitation, not as a defect to guard against.

## Open decision (defaulted, not blocking)

`test_route_creates_topic_marked_dirty` needs a topic the fixture does
not later empty. Default: retarget its assertion to `wrapping`, which
survives, keeping the fixture minimal. The alternative — a second item
routed to `softwrap` — is more faithful to the test's name but grows a
fixture several other tests read. Proceeding on the default unless my
human partner says otherwise.

## Boundaries

**IN:** the `route` branch of the routing reducer, its five red tests,
one new test, three docs.

**OUT:** `unroute`, `reenter`, `requeue`, `close` — unchanged.
Filesystem deletion of a synthesized doc (the reducer is replayed
wholesale by `rebuild_from_ledgers`; a filesystem delete inside it
would make a rebuild destructive). `Snapshot` rows. No ledger schema
change, so no re-sync and no replay of `routing.jsonl`.

## Phase typing for `/runbook`

- **tdd** — reducer change plus the six tests (five updated, one new).
- **inline** — the three doc edits.
