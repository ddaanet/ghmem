# Item 1.1 report — extract routing_helpers.py

## What moved

Created `tools/ghmem/tests/routing_helpers.py`, a pure-scaffolding module
(no `test_` prefix, not collected by pytest), owning:

- `DB_NAME`, `FETCH_FIXTURE`
- `_live_store(tmp_path) -> tuple[Store, Ledger[RoutingEvent]]`
- `_session(tmp_path) -> Session`
- `_dispatch(item_id) -> RoutingEvent`
- `_route(item_id, verdict, updated_at) -> RoutingEvent`

Moved verbatim (byte-identical) out of the five files that carried
duplicate copies:

- `tests/test_routing_live.py` — lost its `_live_store`/`_session`
  (`DISPATCH_1`, `ROUTE_SOFTWRAP`, `ROUTE_WRAPPING` stayed, per scope).
- `tests/test_routing_replay.py` — lost only `_session`; its longer
  docstring ("`Store` doesn't expose `Route`/`Dispatch`/`Topic` queries
  yet (that's Task 4)") was replaced by the shared module's docstring,
  not absorbed. `DB_NAME`/`FETCH_FIXTURE` are still imported directly
  here since the file's own `store` fixture (built from
  `ROUTING_FIXTURE`, not `_live_store`) uses them by name.
- `tests/test_unroute.py` — lost `_live_store`, `_session`,
  `_dispatch`, `_route`, `DB_NAME`, `FETCH_FIXTURE`.
- `tests/test_reenter.py` — lost `_live_store`/`_session`.
- `tests/test_requeue.py` — lost `_live_store`/`_session`; `DB_NAME` is
  still imported directly here since `_corpus_session` builds a path
  with it. `_corpus_session` and the `corpus` fixture stayed, per
  scope (requeue-specific).

Each consumer now does `from tests.routing_helpers import ...`. Unused
imports left behind by the move (`create_engine`, `Session`, `Store`,
`FetchEvent`, `RoutingEvent` — whichever went unused in each specific
file after its own local helpers were removed) were dropped file by
file rather than left dangling.

No test was added, removed, retargeted, or had an assertion edited —
this was a pure move, verified by an unchanged pass count (278 tests,
same as before the change).

## Gate and commit

`just precommit` (run from `/Users/david/code/micro`) passed:

```
✓ ruff check · docformatter -c · mypy · 278 passed · 47 bats · line limits
```

Tree is clean. Commit: `556c51f9e19e3d5dd76c60160a168fa148f6a9dd`.

Note: this repo's gitmoji commit-msg hook rewrote the authored subject
`refactor: Item 1.1 — extract routing_helpers.py` into
`♻️ Item 1.1 — extract routing_helpers.py` (mapping the `refactor:`
prefix to its emoji and dropping the literal prefix text) — the same
transform visible on every other commit in this repo's history. The
hook was not bypassed.
