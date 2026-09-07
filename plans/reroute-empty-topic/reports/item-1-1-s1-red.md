# Item 1.1, slice 1 — RED report

Scope: `tools/ghmem/tests/test_routing_live.py` and
`tools/ghmem/tests/test_routing_replay.py`. No `src/` edit made — the SUT is
untouched. All commands below run from `tools/ghmem/`.

## Flag before the results: one of the seven passes against unchanged code

`test_routing_replay.py::test_route_creates_topic_marked_dirty` (retargeted)
**PASSED** against unchanged `src/`, not failed. Traced this rather than
forcing it red:

- Unchanged `_apply_route_event` (`src/ghmem/routing_reduce.py:125-141`)
  dirties *both* the leaving topic and the joining topic on every route —
  it never drops either. So after the fixture's full sequence (route I_1→
  softwrap, route I_2→no-signal, route I_1→wrapping), both `"softwrap"` and
  `"wrapping"` exist and are dirty under unchanged code — confirmed by the
  `test_rebuild_is_idempotent`/`test_live_writes_match_a_cold_rebuild`
  failures below, whose expected sets shrink from `{"softwrap", "wrapping"}`
  to `{"wrapping"}`.
- The retargeted test only asserts `session.get(Topic, "wrapping")` is not
  None and dirty. That's true under both unchanged and fixed code — the
  join-side dirtying this test actually reads is untouched by this change.
  It doesn't assert `"softwrap"` is gone, so it can't discriminate.
- This matches the runbook's own framing of this specific bullet (item 5 in
  the runbook's slice-1 list): unlike every other bullet in that list, its
  text never claims the retargeted test reds — it says explicitly "This is
  fixture coupling, not a contract change." Every other bullet in the same
  list states an explicit "against unchanged code X fails" for its case;
  this one doesn't.
- The dispatch's "seven affected tests, confirm every one fails" framing
  appears to have generalized the "five probe-confirmed reds" description
  (which is about the *original, unmodified* tests reding once the SUT is
  fixed — the probe ran the reducer fix against old tests, not the reverse)
  onto this test's *retargeted* form, which the runbook's own per-test
  bullet doesn't claim.

Per "genuine red is a failed assertion" and "don't force a pass into a
fail" — I left this test as written (retargeted, docstring rewritten) and
did not touch its assertions to manufacture a red. Six of the seven other
tests are true reds on unchanged code; this one is correctly green both
before and after Item 1.1's implementation, by design, since it pins no
part of this change's contract.

## Post-edit line counts

```
$ wc -l tests/test_routing_live.py tests/test_routing_replay.py
  232 tests/test_routing_live.py
  320 tests/test_routing_replay.py
```

Both well inside the 400-line cap (`test_routing_live.py` was 168, now 232;
`test_routing_replay.py` was 328 pre-dispatch per the brief, now 320 — the
old-topic docstring paragraph deleted in full outweighed the two rewrites).

The `test_routing_live.py` count is 232 rather than the 231 this phase left:
the test review rewrote one docstring paragraph, one line longer. Its
verdicts, its fixes and the post-fix re-run are in
`plans/reroute-empty-topic/reports/item-1-1-s1-test-review.md`; the pytest
transcript below is the red phase's own run and is left as written.

## Full pytest output, the seven tests

```
$ python -m pytest tests/test_routing_live.py::test_status_dirty_set_stays_assemblable_after_a_reroute tests/test_routing_live.py::test_route_to_no_signal_drops_the_emptied_topic tests/test_routing_replay.py::test_reroute_replaces_the_verdict_and_drops_the_emptied_topic tests/test_routing_live.py::test_reroute_of_the_last_item_drops_the_topic_row tests/test_routing_replay.py::test_route_creates_topic_marked_dirty tests/test_routing_replay.py::test_rebuild_is_idempotent tests/test_routing_live.py::test_live_writes_match_a_cold_rebuild -v

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
tests/test_routing_live.py:204: in test_status_dirty_set_stays_assemblable_after_a_reroute
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
tests/test_routing_live.py:230: in test_route_to_no_signal_drops_the_emptied_topic
    assert session.get(Topic, "softwrap") is None
E   AssertionError: assert Topic(name='softwrap', dirty=True) is None
E    +  where Topic(name='softwrap', dirty=True) = get(Topic, 'softwrap')
E    +    where get = <sqlalchemy.orm.session.Session object at 0x7fab95e8cd50>.get
________ test_reroute_replaces_the_verdict_and_drops_the_emptied_topic _________
tests/test_routing_replay.py:87: in test_reroute_replaces_the_verdict_and_drops_the_emptied_topic
    assert softwrap is None
E   AssertionError: assert Topic(name='softwrap', dirty=True) is None
______________ test_reroute_of_the_last_item_drops_the_topic_row _______________
tests/test_routing_live.py:172: in test_reroute_of_the_last_item_drops_the_topic_row
    assert session.get(Topic, "softwrap") is None
E   AssertionError: assert Topic(name='softwrap', dirty=True) is None
E    +  where Topic(name='softwrap', dirty=True) = get(Topic, 'softwrap')
E    +    where get = <sqlalchemy.orm.session.Session object at 0x7fab95e8e850>.get
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
========================= 6 failed, 1 passed in 0.86s ==========================
```

## Per-test summary

| # | Test | Kind | Result |
|---|------|------|--------|
| 1 | `test_routing_live.py::test_status_dirty_set_stays_assemblable_after_a_reroute` | new | RED — assertion (`summary.dirty` still carries `"softwrap"`) |
| 2 | `test_routing_live.py::test_route_to_no_signal_drops_the_emptied_topic` | new | RED — assertion (`"softwrap"` topic still present) |
| 3 | `test_routing_replay.py::test_reroute_replaces_the_verdict_and_drops_the_emptied_topic` | renamed | RED — assertion (`"softwrap"` topic still present) |
| 4 | `test_routing_live.py::test_reroute_of_the_last_item_drops_the_topic_row` | replaces `test_reroute_dirties_the_topic_it_leaves` | RED — assertion (`"softwrap"` topic still present) |
| 5 | `test_routing_replay.py::test_route_creates_topic_marked_dirty` | retargeted | **GREEN** — see flag above; expected, not a contract test |
| 6 | `test_routing_replay.py::test_rebuild_is_idempotent` | retargeted | RED — assertion (topic set still `{"softwrap","wrapping"}`) |
| 7 | `test_routing_live.py::test_live_writes_match_a_cold_rebuild` | retargeted | RED — assertion (topic set still `{"softwrap","wrapping"}`) |

All six reds fail on an `assert` line with a shown actual-vs-expected — none
is an `ERROR`, import failure, or collection failure. Confirmed no
collection issues by also running both full files
(`python -m pytest tests/test_routing_live.py tests/test_routing_replay.py -q`):
`6 failed, 11 passed` — the eleven passes are the six unaffected tests in
these two files plus the one flagged above, all consistent with what's
expected pre-implementation.

## Scope discipline

`tools/ghmem/src/` untouched. `tests/routing_helpers.py` untouched (only
imported from). Nothing committed — working tree left dirty per the RED
override in the dispatch.
