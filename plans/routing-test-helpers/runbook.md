# Runbook — extract `routing_helpers.py`

Split out of `plans/reroute-empty-topic/runbook.md`, which needs this landed
before its own Phase 1: that job adds `route` tests to `test_routing_live.py`
and `test_routing_replay.py`, and without the shared module it would add a
fourth `_live_store` copy and a second copy of the `_dispatch`/`_route`
builders. All paths below are relative to the repository root; the Python
package lives under `tools/ghmem/`.

Requirements are `docs/ghmem-design.md`'s NFR8 — this job authors none of its
own and touches no requirement's behaviour. NFR9's budget does not apply: the
change is "a PR that merely reorganizes code with no behavioral change", which
that requirement exempts outright.

## Requirements mapping

| Requirement | Phase | Items | Notes |
|---|---|---|---|
| NFR8 — the toolchain gate is the bar for every change | 1 | 1.1 | `just precommit` is the whole evidence: a refactor's only claim is that nothing moved, and the unchanged suite passing is that claim |

## Phase 1: the extraction (type: general)

- Item 1.1: `tools/ghmem/tests/routing_helpers.py` — extract the shared routing
  test scaffolding. Requirements: NFR8.
  Model: sonnet

  A pure refactor with the existing green suite as its safety net. No behaviour
  changes, so no test is added, removed or retargeted: any assertion edit
  needed to keep the suite green means the move was not a move, and is the
  signal to stop and report rather than to adjust the test.

  The new module follows the four that already exist, docstring included —
  `triage_helpers.py` states "Not collected by pytest — the module carries no
  `test_` prefix", and this one says the same. It owns `DB_NAME`,
  `FETCH_FIXTURE`, `_live_store`, `_session`, and the `_dispatch(item_id)` /
  `_route(item_id, verdict, updated_at)` builders lifted from
  `test_unroute.py:18-43`. Consumers import as the repo does elsewhere:
  `from tests.routing_helpers import ...`.

  The five files losing their copies are `test_routing_live.py`,
  `test_routing_replay.py`, `test_unroute.py`, `test_reenter.py` and
  `test_requeue.py` — about 60 lines removed in total, which is real headroom
  against `check_line_limits.sh`'s 400-line cap that the reroute job works
  under. Four of the five carry a byte-identical `_live_store`;
  `test_routing_replay.py` carries `_session` alone.

  Two details the move must not paper over. `test_routing_replay.py`'s
  `_session` copy carries a longer docstring citing a limitation that no longer
  holds — "`Store` doesn't expose `Route`/`Dispatch`/`Topic` queries yet (that's
  Task 4)" — and the shared docstring replaces it rather than absorbing it.
  And `test_requeue.py` keeps its own `_corpus_session` and `corpus` fixture,
  which are requeue-specific and not shared scaffolding.

  Interfaces:
  - `_live_store(tmp_path: Path) -> tuple[Store, Ledger[RoutingEvent]]` — moved
    verbatim; four call sites become imports.
  - `_session(tmp_path: Path) -> Session` — moved; five call sites.
  - `_dispatch(item_id: str) -> RoutingEvent` — moved from `test_unroute.py`.
  - `_route(item_id: str, verdict: str, updated_at: str) -> RoutingEvent` —
    moved from `test_unroute.py`.
  - `DB_NAME: str`, `FETCH_FIXTURE: Path` — moved; five call sites each.

- Item 1.2: repository root — run `just precommit` over the whole tree.
  Requirements: NFR8.
  Depends on: Item 1.1

  The gate is this job's only evidence, so read its one-line recap rather than
  its silence. `just green` is cached against every file under `tests/`, so the
  move invalidates the cache and the gate really runs.

## Out of scope

- **The four files that share only `FETCH_FIXTURE`** — `test_cli_unroute.py`,
  `test_dispatch_store.py`, `test_stale_routes.py`, `test_status.py`. This job
  stops at the five files that duplicate helper *functions*; importing a path
  constant is churn with no duplication behind it.
- **The module constants `DISPATCH_1`, `ROUTE_SOFTWRAP` and `ROUTE_WRAPPING`**,
  which stay in `test_routing_live.py` — three existing tests read them, and
  replacing them is churn.
- **Any behaviour change**, in the reducer or anywhere else. That is
  `plans/reroute-empty-topic/`.
