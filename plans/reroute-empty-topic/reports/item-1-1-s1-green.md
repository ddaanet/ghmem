# Item 1.1, slice 1 — GREEN report

Implementation: `tools/ghmem/src/ghmem/routing_reduce.py`,
`tools/ghmem/src/ghmem/ledger.py`. Tests: already-written
`tools/ghmem/tests/test_routing_live.py`,
`tools/ghmem/tests/test_routing_replay.py`, committed unedited.

## The four edits

- `_apply_route_event` — captures `left = previous.verdict if previous is not
  None else None` before `session.merge`, then calls
  `_release_topic(session, left)` after the merge and after the join-side
  `_mark_topic_dirty`, guarded on `left is not None and left != NO_SIGNAL`.
  Summary line: `Replace an item's verdict, releasing the topic it leaves.`
- `_release_topic` — deleted the "Scoped to `"unroute"`" paragraph and the
  open-question sentence; states the rule as covering withdrawal and
  re-route alike. Reworded the autoflush comment to name both pending-write
  shapes (`delete` under `unroute`, `update` under `route`) and added the
  concurrency argument (writer-lock ordering keeps `COUNT` and `DELETE` on
  the same side of a concurrent sibling route). Softened the
  `docs/design/<name>.md` paragraph, dropping the now-false "remains true
  prose about items that really were routed there" clause. `Snapshot`
  paragraph left unchanged.
- `_mark_topic_dirty` — now a conditional upsert via `session.execute()` and
  `sqlalchemy.dialects.sqlite.insert` (imported as `sqlite_insert`, matching
  `dispatch_store.py:20`), docstring pointing at `reserve_dispatch`'s
  reasoning and recording that `session.execute()`'s autoflush is
  load-bearing for the within-replay delete-then-recreate case.
- `ledger.py`'s `NO_SIGNAL` docstring — added the one sentence: routing to it
  still releases the topic the item leaves.

Widened beyond the runbook's four bullets by one docstring-only fix, in
scope (same file, not a listed "don't touch"): `apply_routing_event`'s own
docstring described `"route"` as dirtying both topics it leaves and joins,
which this change falsifies for the topic it leaves. Reworded to describe
the join-side dirty and the leave-side release, pointing at the `"unroute"`
paragraph below it rather than restating it. No test references this text;
found by inspection, not required to make any test pass.

## Verification

Six reds green, seventh (`test_route_creates_topic_marked_dirty`) still
green:

```
tests/test_routing_live.py::test_status_dirty_set_stays_assemblable_after_a_reroute PASSED
tests/test_routing_live.py::test_route_to_no_signal_drops_the_emptied_topic PASSED
tests/test_routing_replay.py::test_reroute_replaces_the_verdict_and_drops_the_emptied_topic PASSED
tests/test_routing_live.py::test_reroute_of_the_last_item_drops_the_topic_row PASSED
tests/test_routing_replay.py::test_rebuild_is_idempotent PASSED
tests/test_routing_live.py::test_live_writes_match_a_cold_rebuild PASSED
tests/test_routing_replay.py::test_route_creates_topic_marked_dirty PASSED
7 passed in 0.86s
```

Full `tools/ghmem` suite: `280 passed`. `test_unroute.py`, `test_reenter.py`,
`test_requeue.py`, `test_cli_close.py`, `test_dispatch_store.py` all passed
**without edits** — confirmed by `git status --short` before commit, showing
only the two `src/` files and the two already-modified test files.

`wc -l tools/ghmem/src/ghmem/routing_reduce.py` → **351** (was 337; cap 400,
49 lines clear).

`just precommit` from the repository root:

```
✓ ruff check · docformatter -c · mypy · 280 passed · 47 bats · line limits
```

## Commit

`921494b308863fd02a82a4e828438f52b6b77b52` — subject
`✨ Item 1.1/1 — an emptied topic is dropped` (the repo's gitmoji
commit-msg hook rewrote the `feat:` prefix to `✨` per its own convention;
not a deviation from what was requested). Carries the implementation and
the tests together. Staged only `tools/ghmem/src/` and `tools/ghmem/tests/`
paths (not `git add -A`). Tree clean afterward.
