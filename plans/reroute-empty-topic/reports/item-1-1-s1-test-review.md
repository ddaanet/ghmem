# Item 1.1, slice 1 — test review

Scope reviewed: `tools/ghmem/tests/test_routing_live.py`,
`tools/ghmem/tests/test_routing_replay.py`, and
`plans/reroute-empty-topic/reports/item-1-1-s1-red.md`. `tools/ghmem/src/`
untouched; `tests/routing_helpers.py` and `tests/fixtures/routing_events.jsonl`
untouched. Nothing committed.

One major finding, fixed. Everything else passed as written.

## Mechanical check

Six reds, one green, as the dispatch established. Every one of the six fails on
an `assert` line with a shown actual-vs-expected — no `ERROR`, no collection or
import failure. Verbatim output of the post-fix re-run is at the bottom of this
report.

The seventh, `test_routing_replay.py::test_route_creates_topic_marked_dirty`,
is green, and I confirmed **both** sides of the reason rather than only the
one the RED report traced:

- *Old side* (the RED report's argument): unchanged `_apply_route_event`
  (`src/ghmem/routing_reduce.py:126-141`) dirties the leaving topic and the
  joining topic and drops neither, so after the fixture's sequence both
  `"softwrap"` and `"wrapping"` exist and are dirty. The retargeted test reads
  only the join-side dirtying of `"wrapping"`, which this change does not
  touch.
- *New side* (not established in the RED report, so I probed it): under the
  runbook's intended `_apply_route_event` — `left` captured before the merge,
  `_release_topic(session, left)` after it — a cold rebuild from the shared
  fixture leaves `topics == ['wrapping']`,
  `Topic(name='wrapping', dirty=True)`, and `I_1`'s stamp
  `2026-01-01T00:00:00+00:00`. All three of the test's assertions hold after
  the implementation lands too.

So it is green for the stated reason and no other, before and after. I did not
touch its assertions.

The probe monkeypatched `routing_reduce._apply_route_event` **in memory only**,
from a script under `$TMPDIR`; no file under `tools/ghmem/src/` was written at
any point (`git diff --stat -- tools/ghmem/src/` is empty).

## Per-check verdicts

### 1. `test_status_dirty_set_stays_assemblable_after_a_reroute` — PASS

Structurally `test_unroute.py:238`
(`test_status_dirty_set_stays_assemblable_after_emptying_a_topic`) with a
re-route as its final event. Diffed the two bodies: identical setup loop —
`I_1`→`"softwrap"`, `I_2`→`"wrapping"`, `I_3`→`"wrapping"`, each preceded by
`_dispatch(item_id)` — identical docstring, identical three assertions; the
only difference is the final event, `_route("I_1", "wrapping", ...)` here
against `UNROUTE_1` there. All three required assertions are present
(`tools/ghmem/tests/test_routing_live.py:204-207`):
`summary.dirty == ("wrapping",)`, `summary.topics == ("wrapping",)`, and the
`assemble` loop.

The `assemble` half is **not vacuous**: the loop iterates `summary.dirty`, and
the line immediately above pins that tuple to `("wrapping",)`, which runs
first — so either the equality fails, or the loop has exactly one name to
assemble. `assemble` raises `TopicNotRoutedError` when `store.routed_items(name)`
is empty (`src/ghmem/topic.py:51-53`), which is the breakage this test exists to
refuse.

Noted, not a defect: the loop's discrimination is partly subsumed by the
equality above it — an implementation that left `"softwrap"` dirty fails at
line 204 before the loop runs. The loop still carries the FR4/FR6 claim the
equality does not (that the names `status` hands out are assemblable, not
merely that the set has the expected contents), and it is exactly the sibling's
shape. Left as written.

### 2. `test_route_to_no_signal_drops_the_emptied_topic` — PASS, probe-confirmed

The second assertion can red, and does. Probed by running the test's exact
scenario against two in-memory implementations of the intended
`_apply_route_event`, one with the join-side `data["verdict"] != NO_SIGNAL`
guard and one without:

```
guard kept (intended impl): softwrap=None no-signal=None
    -> assert1 PASS, assert2 PASS
guard DROPPED (mutation):   softwrap=None no-signal=Topic(name='no-signal', dirty=True)
    -> assert1 PASS, assert2 FAIL
```

Two things this settles. The mutation mints `Topic("no-signal")` and nothing
drops it — the release runs against `left="softwrap"`, never against
`NO_SIGNAL` — so the assertion has a state of the world in which it fails. And
the *first* assertion still passes under that mutation, so it does not abort
the test before the second is reached: the second's result is its own.

### 3. `test_reroute_of_the_last_item_drops_the_topic_row` — PASS

`test_reroute_dirties_the_topic_it_leaves` is gone, replaced. The
`dirty = False` cleaning step and its `_session` block are deleted in full —
confirmed in the diff, and the body is now two `record_routing_event` calls
followed by the two assertions. Nothing in the file still cleans a topic before
an `is None` assertion.

### 4. `test_rebuild_is_idempotent` and `test_live_writes_match_a_cold_rebuild` — PASS

One edit to each, the hardcoded set only: `{"softwrap", "wrapping"}` →
`{"wrapping"}` (`test_routing_replay.py:319`, `test_routing_live.py:148`). Both
stayed non-empty, so the non-vacuity guard on the two `derived_a == derived_b`
comparisons survives. No separate `session.get(Topic, "softwrap") is None` was
added to either — the runbook forbids it, and the diff shows no such line.
`test_live_writes_match_a_cold_rebuild` also keeps its `len(routes) == 1` and
`len(dispatches) == 1` guards.

### 5. Docstrings — one MAJOR finding, fixed

**Fixed:** `test_reroute_of_the_last_item_drops_the_topic_row` carried a
falsified paragraph — the old mutation note reworded rather than deleted:

> only reachable live: a rebuild starts from a dropped schema, so the topic an
> item leaves was always created dirty by the route that put it there, and
> there is no standing row for a rebuild to drop.

This is false after the change, and the file next door proves it: the replay
counterpart `test_reroute_replaces_the_verdict_and_drops_the_emptied_topic`
asserts `softwrap is None` **from a rebuild**, and is one of the six reds.
Probed to be sure rather than arguing it: under the intended implementation a
cold rebuild from the shared fixture prints `softwrap after rebuild: None` —
the row is created by the first route event and dropped by the third, inside
one replay. So a standing row *is* there for the rebuild to drop, and the
behaviour is not "only reachable live".

This is precisely the sentence the dispatch's check 5 names ("dropping the
reducer's old-topic dirtying changes nothing observable"), carried across the
rewrite in new words. Replaced with a positive statement of the surviving rule
plus the reason this live test earns its place beside the replay one — no
account of what the docstring used to say:

```
    A Topic row exists exactly while some Route names it, whichever
    event empties it: `unroute`'s withdrawal and a re-route away are
    one rule. This is the live counterpart of `test_routing_replay.py`'s
    `test_reroute_replaces_the_verdict_and_drops_the_emptied_topic` —
    the same rule reached one event at a time through
    `record_routing_event` rather than through a whole-ledger replay.
```

The other two mandated rewrites are clean:

- `test_reroute_replaces_the_verdict_and_drops_the_emptied_topic` — the
  paragraph beginning "The *old*-topic half of the reducer is NOT proven here"
  is **deleted in full**, not reworded. Summary is now "A re-route is
  latest-wins, and drops the topic it empties."; the `Then:` line states the
  new outcome.
- `test_route_creates_topic_marked_dirty` — `When:`/`Then:` both retargeted;
  the summary ("Routing an item to a new topic creates it, marked dirty")
  survives and is still true, since `"wrapping"` is genuinely new at the
  fixture's last event.

Swept both files for any other falsified sentence
(`softwrap|both|dirt|clean|observable|used to|no longer|previously`): every
remaining hit is either an unrelated docstring or a still-true statement. In
particular nothing anywhere claims a re-route "leaves both topics dirty", and
`test_rebuild_discards_state_absent_from_the_ledgers` already asserted on
`"wrapping"` and needed no edit.

Kept `FR-4` in the new test's docstring rather than normalizing to the
runbook's `FR4`: it is copied verbatim from the sibling at
`test_unroute.py:241`, and changing one of the pair would put them out of step.

### 6. Scaffolding — PASS

`grep -E "^(DB_NAME|FETCH_FIXTURE)|^def (_live_store|_session|_dispatch|_route)"`
over both files returns nothing: no local re-definition of any shared helper.
`test_routing_live.py` imports `FETCH_FIXTURE`, `_dispatch`, `_live_store`,
`_route`, `_session` from `tests.routing_helpers`; `test_routing_replay.py`
imports `DB_NAME`, `FETCH_FIXTURE`, `_session`. `DISPATCH_1` (`:24`),
`ROUTE_SOFTWRAP` (`:34`) and `ROUTE_WRAPPING` (`:44`) remain module constants in
`test_routing_live.py`.

### 7. Line counts — PASS

```
$ wc -l tests/test_routing_live.py tests/test_routing_replay.py
  232 tests/test_routing_live.py
  320 tests/test_routing_replay.py
```

Both inside `scripts/check_line_limits.sh`'s 400-line cap.
`test_routing_live.py` is 232 rather than the RED report's 231: the docstring
fix above is one line longer than what it replaced. That report's count block
is corrected accordingly.

## Fixes applied

1. **MAJOR** — `tools/ghmem/tests/test_routing_live.py:155-160`: rewrote the
   second paragraph of `test_reroute_of_the_last_item_drops_the_topic_row`'s
   docstring, which asserted the change is "only reachable live" and that
   "there is no standing row for a rebuild to drop". Both false after this
   change, and disproved by probe. Replaced with a positive statement of the
   invariant and of what this test adds over its replay counterpart.
2. **MINOR** — `plans/reroute-empty-topic/reports/item-1-1-s1-red.md`: updated
   the post-edit line count from 231 to 232 and added one sentence naming the
   cause and pointing at this report. The pytest transcript in that report is
   the red phase's own run and is left verbatim.

No other fix was warranted — no critical finding, and nothing in the slice
required an `src/` change to make a test correct.

## Ancillary checks

Run because the two test files gained new imports in this slice, and a
formatting failure would surface as a gate failure later rather than here:

```
$ ruff format --check tests/test_routing_live.py tests/test_routing_replay.py
2 files already formatted
$ ruff check tests/test_routing_live.py tests/test_routing_replay.py
All checks passed!
$ docformatter -c --diff tests/test_routing_live.py tests/test_routing_replay.py
(clean)
$ mypy
(clean, exit 0)
```

`just precommit` was **not** run — the suite is meant to be red.

## Post-fix re-run, verbatim

Run from `tools/ghmem/`.

```
$ .venv/bin/python -m pytest tests/test_routing_live.py::test_status_dirty_set_stays_assemblable_after_a_reroute tests/test_routing_live.py::test_route_to_no_signal_drops_the_emptied_topic tests/test_routing_replay.py::test_reroute_replaces_the_verdict_and_drops_the_emptied_topic tests/test_routing_live.py::test_reroute_of_the_last_item_drops_the_topic_row tests/test_routing_replay.py::test_route_creates_topic_marked_dirty tests/test_routing_replay.py::test_rebuild_is_idempotent tests/test_routing_live.py::test_live_writes_match_a_cold_rebuild -v

============================= test session starts ==============================
collecting ... collected 7 items

tests/test_routing_live.py::test_status_dirty_set_stays_assemblable_after_a_reroute FAILED [ 14%]
tests/test_routing_live.py::test_route_to_no_signal_drops_the_emptied_topic FAILED [ 28%]
tests/test_routing_replay.py::test_reroute_replaces_the_verdict_and_drops_the_emptied_topic FAILED [ 42%]
tests/test_routing_live.py::test_reroute_of_the_last_item_drops_the_topic_row FAILED [ 57%]
tests/test_routing_replay.py::test_route_creates_topic_marked_dirty PASSED [ 71%]
tests/test_routing_replay.py::test_rebuild_is_idempotent FAILED          [ 85%]
tests/test_routing_live.py::test_live_writes_match_a_cold_rebuild FAILED [100%]

=================================== FAILURES ===================================
___________ test_status_dirty_set_stays_assemblable_after_a_reroute ____________
tests/test_routing_live.py:205: in test_status_dirty_set_stays_assemblable_after_a_reroute
    assert summary.dirty == ("wrapping",)
E   AssertionError: assert ('softwrap', 'wrapping') == ('wrapping',)
E
E     At index 0 diff: 'softwrap' != 'wrapping'
E     Left contains one more item: 'wrapping'
E
E     Full diff:
E       (
E     +     'softwrap',
E           'wrapping',
E       )
_______________ test_route_to_no_signal_drops_the_emptied_topic ________________
tests/test_routing_live.py:231: in test_route_to_no_signal_drops_the_emptied_topic
    assert session.get(Topic, "softwrap") is None
E   AssertionError: assert Topic(name='softwrap', dirty=True) is None
E    +  where Topic(name='softwrap', dirty=True) = get(Topic, 'softwrap')
E    +    where get = <sqlalchemy.orm.session.Session object at 0x7f437e8a8d50>.get
________ test_reroute_replaces_the_verdict_and_drops_the_emptied_topic _________
tests/test_routing_replay.py:87: in test_reroute_replaces_the_verdict_and_drops_the_emptied_topic
    assert softwrap is None
E   AssertionError: assert Topic(name='softwrap', dirty=True) is None
______________ test_reroute_of_the_last_item_drops_the_topic_row _______________
tests/test_routing_live.py:173: in test_reroute_of_the_last_item_drops_the_topic_row
    assert session.get(Topic, "softwrap") is None
E   AssertionError: assert Topic(name='softwrap', dirty=True) is None
E    +  where Topic(name='softwrap', dirty=True) = get(Topic, 'softwrap')
E    +    where get = <sqlalchemy.orm.session.Session object at 0x7f437e8aa850>.get
__________________________ test_rebuild_is_idempotent __________________________
tests/test_routing_replay.py:319: in test_rebuild_is_idempotent
    assert {t.name for t in topics} == {"wrapping"}
E   AssertionError: assert {'softwrap', 'wrapping'} == {'wrapping'}
E
E     Extra items in the left set:
E     'softwrap'
E
E     Full diff:
E       {
E     +     'softwrap',
E           'wrapping',
E       }
____________________ test_live_writes_match_a_cold_rebuild _____________________
tests/test_routing_live.py:148: in test_live_writes_match_a_cold_rebuild
    assert {t.name for t in topics} == {"wrapping"}
E   AssertionError: assert {'softwrap', 'wrapping'} == {'wrapping'}
E
E     Extra items in the left set:
E     'softwrap'
E
E     Full diff:
E       {
E     +     'softwrap',
E           'wrapping',
E       }
=========================== short test summary info ============================
FAILED tests/test_routing_live.py::test_status_dirty_set_stays_assemblable_after_a_reroute
FAILED tests/test_routing_live.py::test_route_to_no_signal_drops_the_emptied_topic
FAILED tests/test_routing_replay.py::test_reroute_replaces_the_verdict_and_drops_the_emptied_topic
FAILED tests/test_routing_live.py::test_reroute_of_the_last_item_drops_the_topic_row
FAILED tests/test_routing_replay.py::test_rebuild_is_idempotent - AssertionEr...
FAILED tests/test_routing_live.py::test_live_writes_match_a_cold_rebuild - As...
========================= 6 failed, 1 passed in 1.01s ==========================
```

Both files whole, to confirm nothing collateral moved:

```
$ .venv/bin/python -m pytest tests/test_routing_live.py tests/test_routing_replay.py -q
6 failed, 11 passed in 1.34s
```

Only the docstring line numbers shifted against the RED report's transcript
(`:204`→`:205`, `:230`→`:231`, `:172`→`:173`); the failing assertions and their
messages are unchanged.

## Scope discipline

`git status --short` at hand-back:

```
 M tools/ghmem/tests/test_routing_live.py
 M tools/ghmem/tests/test_routing_replay.py
?? plans/reroute-empty-topic/reports/item-1-1-s1-red.md
?? plans/reroute-empty-topic/reports/item-1-1-s1-test-review.md
```

`git diff --stat -- tools/ghmem/src/` is empty. Nothing committed.
