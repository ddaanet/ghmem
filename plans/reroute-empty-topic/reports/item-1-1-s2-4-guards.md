# Item 1.1, slices 2–4 — regression guards and the mutation matrix

Four commits:

- `ef67714` — `test: Item 1.1/2 — a topic that keeps a sibling survives`
- `06ec547` — `test: Item 1.1/3 — a same-topic re-route keeps the topic and the route`
- `aafb0ae` — `test: Item 1.1/4 — a topic re-minted in one replay survives`
- `466d818` — `docs: Item 1.1/2-4 — record the mutation results`

Final `just precommit` recap:

```
✓ ruff check · docformatter -c · mypy · 283 passed · 47 bats · line limits
```

Final `wc -l`:

```
330 tools/ghmem/tests/test_routing_live.py
365 tools/ghmem/tests/test_routing_replay.py
```

Both clear of the 400-line cap.

## Slices, as landed

All three were green the moment written, per the runbook — no red phase,
each named for the mutation it discriminates rather than for a red it
produced.

- `test_reroute_keeps_a_topic_that_keeps_items` (`test_routing_live.py`) —
  routes #1 and #2 to `"softwrap"`, marks it clean, re-routes #1 to
  `"wrapping"`, asserts `"softwrap"` survives dirty.
- `test_same_topic_reroute_keeps_the_topic_and_the_route`
  (`test_routing_live.py`) — routes #1 to `"softwrap"`, marks it clean,
  records a second `route` event sending #1 to `"softwrap"` again from a
  different session (`"s2"`) with a different note, asserts the topic
  stays dirty and the `Route` row matches the second event in full.
- `test_a_topic_reminted_after_being_emptied_in_one_replay_survives`
  (`test_routing_replay.py`) — builds its own ledger (`reroute_routing.jsonl`
  under `tmp_path`, not the shared fixture), four `route` events with no
  `dispatched` events, replayed in one `rebuild_from_ledgers` call; asserts
  both `"softwrap"` and `"wrapping"` exist and are dirty.

## The 3×3 mutation matrix

Method for each: `git checkout -- tools/ghmem/src/ghmem/routing_reduce.py`
before and after, mutation applied in place, never a relocated test.

### M2 — route-side release skips `_mark_topic_dirty` on `_release_topic`'s `remaining` branch

Implementation: added a `dirty_remaining: bool = True` keyword to
`_release_topic`, called as `_release_topic(session, left,
dirty_remaining=False)` from `_apply_route_event`; the `remaining` branch
only calls `_mark_topic_dirty` when `dirty_remaining` is true.

```
============================= test session starts ==============================
collecting ... collected 4 items

tests/test_routing_live.py::test_reroute_keeps_a_topic_that_keeps_items FAILED [ 25%]
tests/test_routing_live.py::test_same_topic_reroute_keeps_the_topic_and_the_route PASSED [ 50%]
tests/test_routing_replay.py::test_a_topic_reminted_after_being_emptied_in_one_replay_survives PASSED [ 75%]
tests/test_unroute.py::test_unroute_dirties_a_topic_that_keeps_items PASSED [100%]

=================================== FAILURES ===================================
_________________ test_reroute_keeps_a_topic_that_keeps_items __________________
tests/test_routing_live.py:242: in test_reroute_keeps_a_topic_that_keeps_items
    assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)
E   AssertionError: assert Topic(name='s..., dirty=False) == Topic(name='s...', dirty=True)
E     
E     Omitting 1 identical items, use -vv to show
E     Differing attributes:
E     ['dirty']
E     
E     Drill down into differing attribute dirty:
E       dirty: False != True
=========================== short test summary info ============================
FAILED tests/test_routing_live.py::test_reroute_keeps_a_topic_that_keeps_items
========================= 1 failed, 3 passed in 1.53s ==========================
```

**Result: reds slice 2's test alone, exactly as claimed.** Slice 3, slice 4,
and `test_unroute_dirties_a_topic_that_keeps_items` all stayed green — the
M2 check on the `unroute`-side test passes: it covers the `unroute` branch,
not this one.

### M3 — release written as delete-then-recreate instead of the upsert

The runbook's wording ("a release written as the delete-then-recreate
`_apply_unroute_event` uses") is ambiguous between two readings, and I ran
both rather than picking one and hiding the alternative:

**Reading A** — `_release_topic`'s own Topic-row handling rewritten as an
unconditional delete followed by a conditional re-add, instead of calling
`_mark_topic_dirty`:

```python
remaining = session.scalar(...)
topic = session.get(Topic, name)
if topic is not None:
    session.delete(topic)
if remaining:
    session.add(Topic(name=name, dirty=True))
```

**Reading B** — the Route replacement in `_apply_route_event` rewritten as
delete-then-add instead of `session.merge`, mirroring how
`_apply_unroute_event` deletes its own `Route` row:

```python
if previous is not None:
    session.delete(previous)
session.add(Route(item_id=item_id, verdict=..., note=..., session=..., item_updated_at=...))
```

Both, run against the three guard tests:

```
============================= test session starts ==============================
collecting ... collected 4 items

tests/test_routing_live.py::test_reroute_keeps_a_topic_that_keeps_items PASSED [ 25%]
tests/test_routing_live.py::test_same_topic_reroute_keeps_the_topic_and_the_route PASSED [ 50%]
tests/test_routing_replay.py::test_a_topic_reminted_after_being_emptied_in_one_replay_survives PASSED [ 75%]
tests/test_unroute.py::test_unroute_dirties_a_topic_that_keeps_items PASSED [100%]

============================== 4 passed in 1.36s ===============================
```

(Reading B's run: `4 passed in 0.78s`, identical outcome.)

Widened to the full routing suites under Reading B, in case the
discrimination showed up somewhere else in the batch:

```
.............................                                            [100%]
29 passed in 2.09s
```

**Result: neither reading reds anything, anywhere in the routing suites.**
SQLAlchemy orders the delete before the insert for the same primary key
within one flush regardless of which table carries the delete-then-recreate,
so no collision or wipe was observed under either reading — the mechanism
the runbook's prose describes ("the pending DELETE can wipe or collide with
the row the merge just wrote") did not reproduce under either construction I
could build from the wording. This is the honest outcome, not something to
engineer around: `test_same_topic_reroute_keeps_the_topic_and_the_route`
does not discriminate the mutation it was named for, as either of us could
construct it. Recorded in the test's docstring; flagging for my human
partner's decision, same as the M4 case below — find a scenario that does
discriminate, or drop the claim.

### M4 — upsert issued on the connection instead of through `session.execute()`

Implementation: `session.connection().execute(stmt)` in place of
`session.execute(stmt)` in `_mark_topic_dirty`.

```
============================= test session starts ==============================
collecting ... collected 4 items

tests/test_routing_live.py::test_reroute_keeps_a_topic_that_keeps_items PASSED [ 25%]
tests/test_routing_live.py::test_same_topic_reroute_keeps_the_topic_and_the_route PASSED [ 50%]
tests/test_routing_replay.py::test_a_topic_reminted_after_being_emptied_in_one_replay_survives PASSED [ 75%]
tests/test_unroute.py::test_unroute_dirties_a_topic_that_keeps_items PASSED [100%]

============================== 4 passed in 1.71s ===============================
```

Widened to the routing, `unroute`, `reenter` and `requeue` suites:

```
........................................                                 [100%]
40 passed in 3.40s
```

**Result: does not red, as the runbook's own "Revised at execution" note and
slice 1's code review anticipated.** The reducer's own call path already
flushes the pending `Topic` DELETE via `session.merge`'s autoflush at the
top of the *next* `_apply_route_event`, before the connection-issued upsert
is ever reached — the same intervening-autoflush reason the recall
checkpoint found the old get-then-add stayed green on this scenario. This
test claims coverage of the upsert it does not have as written. Recorded in
its docstring per the runbook's explicit instruction not to delete or
rewrite it, and flagging it here: the honest outcomes are to find a
scenario that does discriminate the upsert, or to drop the test — my human
partner's decision, not the executor's.

## `src/` restoration

After every mutation, `git checkout -- tools/ghmem/src/ghmem/routing_reduce.py`
restored the file. Confirmed clean after the last one:

```
$ git diff -- tools/ghmem/src/
$ git status --short
$ diff <(git show HEAD:tools/ghmem/src/ghmem/routing_reduce.py) tools/ghmem/src/ghmem/routing_reduce.py
identical to HEAD
```

No `src/` change is committed anywhere in this dispatch; only the two test
files and their docstrings.

## Summary for the decision this leaves open

Two of the three named mutations discriminate as designed (M2 fully; M4's
non-discrimination was already anticipated and is now confirmed). The third
(M3) does not discriminate under either construction I could build from the
runbook's wording, and I could not find a scenario that would make the
delete-then-recreate shape corrupt the `Route` row the way the prose
describes — SQLAlchemy's flush ordering handles same-PK delete-then-insert
correctly regardless of which table carries it. Both
`test_same_topic_reroute_keeps_the_topic_and_the_route` and
`test_a_topic_reminted_after_being_emptied_in_one_replay_survives` now carry
honest docstring records of what their mutation runs actually showed, and
both are left as tests to a human decision rather than deleted or rewritten
on my own initiative, per the runbook's explicit instruction for the M4 case
(extended here to M3, the same shape of finding).
