# ghmem Part D — Re-entry on Corpus Change — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a routed item whose GitHub thread changes (new comment, state flip) return to the triage pending pool automatically, so the next sync→rebuild→triage→synthesize cycle re-judges it and refreshes its topic doc.

**Architecture:** Stamp each `Route` with the item's `updated_at` at verdict time (`Route.item_updated_at`). A new `dispatch_store.stale_routes` query finds routes whose item has moved past that stamp and still carries an outstanding `Dispatch`; `ghmem rebuild` appends a `ReenterEvent` for each, which frees the `Dispatch` and dirties the topic. `untriaged_heat_scores` only excludes a *fresh* Route, so the item rejoins the queue and flows through `serve_next`/`record_verdict` exactly as any untriaged item does.

**Tech Stack:** Python 3.14, SQLAlchemy 2 (declarative ORM, SQLite), msgspec (TypedDict validation of ledger events), click (CLI), pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-ghmem-d-reentry-design.md` (commit `b77a6dc`, user-approved).

## Global Constraints

- `Route.item_updated_at` is `NOT NULL` from the day the column ships — no nullable/rollout-cutoff scaffolding. Every route event from Task 1 onward must carry `item_updated_at`.
- `ReenterEvent` carries no verdict and asserts nothing about staleness — it is a mechanical "free this item's dispatch and dirty its topic" instruction. Staleness is never written to the ledger, only computed by reading `Route.item_updated_at` against the live `IssueOrPR.updated_at`.
- No new `ghmem` CLI command for either the detection pass (folds into `rebuild`) or the backfill (a one-off script, never a maintained subcommand).
- Follow this codebase's existing convention of a full docstring on every function, TypedDict, and non-trivial test — the code blocks below are written in that style; match it, don't strip it down to the terser default.
- Every new/changed file lives under `tools/ghmem/` (`src/ghmem/` for code, `tests/` for tests, matching the existing split — no separate top-level test tree).

---

### Task 1: Ledger schema, `Route` model column, route-branch stamping, and fixture migration

**Files:**
- Modify: `tools/ghmem/src/ghmem/ledger.py:199-206` (`RouteData`)
- Modify: `tools/ghmem/src/ghmem/models.py:276-282` (`Route`)
- Modify: `tools/ghmem/src/ghmem/ingest.py` (imports; `"route"` branch of `apply_routing_event`)
- Modify: `tools/ghmem/src/ghmem/triage.py:246-303` (`record_verdict`)
- Modify (fixture/literal migration, full inventory below): `tests/topic_helpers.py`, `tests/test_queue.py`, `tests/test_routing_live.py`, `tests/test_index_cmd.py`, `tests/test_cli_topic.py`, `tests/test_triage.py`, `tests/test_cli_close.py`, `tests/test_status.py`, `tests/fixtures/status_routing.jsonl`, `tests/fixtures/routing_events.jsonl`
- Modify (new assertions): `tests/test_routing_replay.py`, `tests/test_routing_live.py`

**Interfaces:**
- Produces: `Route.item_updated_at: Mapped[datetime]` (a real column later tasks query); `RouteData["item_updated_at"]: str` (ISO 8601, required in every `route` event from here on).
- Consumes: nothing from other tasks — this is the foundation task.

This task has a wide blast radius: `RouteData.item_updated_at` becomes a *required* TypedDict field (no `NotRequired`), so every existing `route`-event literal in the test suite fails `msgspec.convert` validation the moment the schema lands. The red step here is the *existing* suite going red, not one new failing test — that failure is the proof the type change is actually enforced.

- [ ] **Step 1: Edit `ledger.py` — add `item_updated_at` to `RouteData`**

```python
class RouteData(TypedDict):
    """A triage verdict for one item, to a topic or to `NO_SIGNAL`."""

    session: str
    item_id: str
    verdict: str
    note: str
    item_updated_at: str  # ISO 8601, the item's updated_at at verdict time
```

- [ ] **Step 2: Edit `models.py` — add the `Route` column**

```python
class Route(Base):
    """The current triage verdict for one item (latest-wins by item)."""

    item_id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    verdict: Mapped[str]
    note: Mapped[str]
    session: Mapped[str]
    item_updated_at: Mapped[datetime]
```

(`models.py` already imports `datetime` — no new import needed there.)

- [ ] **Step 3: Edit `ingest.py` — import `datetime` and stamp the column**

Add the import (stdlib `datetime` sorts before stdlib `typing`):

```python
from datetime import datetime
from typing import Final
```

Change the `"route"` branch of `apply_routing_event`:

```python
    if event["type"] == "route":
        data = event["data"]
        item_id = GraphQLId(data["item_id"])
        previous = session.get(Route, item_id)
        if previous is not None and previous.verdict != NO_SIGNAL:
            _mark_topic_dirty(session, previous.verdict)
        session.merge(
            Route(
                item_id=item_id,
                verdict=data["verdict"],
                note=data["note"],
                session=data["session"],
                item_updated_at=datetime.fromisoformat(data["item_updated_at"]),
            )
        )
        if data["verdict"] != NO_SIGNAL:
            _mark_topic_dirty(session, data["verdict"])
        return
```

- [ ] **Step 4: Edit `triage.py` — stamp `item_updated_at` in `record_verdict`**

Change the top of `record_verdict` (keep the whole `IssueOrPR`, not just its `.id`):

```python
    try:
        item = store.get_issue_or_pr(number)
    except IssueOrPRNotFoundError as error:
        raise TriageSessionError.unknown_number(number) from error
    item_id = item.id
```

Change the `RouteEvent` construction at the bottom:

```python
    store.record_routing_event(
        routing,
        RouteEvent(
            type="route",
            data={
                "session": session_token,
                "item_id": item_id,
                "verdict": verdict,
                "note": note,
                "item_updated_at": item.updated_at.isoformat(),
            },
        ),
    )
```

- [ ] **Step 5: Run the full suite and confirm widespread failure**

```bash
cd tools/ghmem && pytest -q
```

Expected: many failures, mostly `msgspec.ValidationError` (missing `item_updated_at` on `route` events) and a few `TypeError`s from `Route(...)` construction. This is the red step — it proves the schema is enforced, not merely declared.

- [ ] **Step 6: Fix every fixture and inline literal**

Every `route`-type event below needs one new key. Where the value is inconsequential to the test (none of these except `test_triage.py` check staleness), use the fixed placeholder `"2026-01-01T00:00:00Z"`; where a test's fixture pairs a specific item with a specific `updatedAt`, match it so the fixture stays internally coherent.

`tests/topic_helpers.py` — in `_route()` (adds `"item_updated_at": "2026-01-01T00:00:00Z"` to the `data` dict):

```python
        RouteEvent(
            type="route",
            data={
                "session": "s1",
                "item_id": item_id,
                "verdict": "softwrap",
                "note": note,
                "item_updated_at": "2026-01-01T00:00:00Z",
            },
        ),
```

and in `_route_no_signal()`:

```python
        RouteEvent(
            type="route",
            data={
                "session": "s1",
                "item_id": item_id,
                "verdict": NO_SIGNAL,
                "note": "nothing decided here",
                "item_updated_at": "2026-01-01T00:00:00Z",
            },
        ),
```

`tests/test_queue.py` — in `test_routed_item_is_absent`, add `"item_updated_at": "2026-01-01T00:00:00Z"` to both of the two `RouteEvent` literals' `data` dicts (item `I_1` and item `I_2`).

`tests/test_routing_live.py` — module-level constants (both pair with `routing_fetch.jsonl`'s `I_1`, whose `updatedAt` is `"2026-01-01T00:00:00Z"`):

```python
ROUTE_SOFTWRAP: RouteEvent = {
    "type": "route",
    "data": {
        "session": "s1",
        "item_id": "I_1",
        "verdict": "softwrap",
        "note": "relates to softwrap",
        "item_updated_at": "2026-01-01T00:00:00Z",
    },
}
ROUTE_WRAPPING: RouteEvent = {
    "type": "route",
    "data": {
        "session": "s2",
        "item_id": "I_1",
        "verdict": "wrapping",
        "note": "actually about wrapping",
        "item_updated_at": "2026-01-01T00:00:00Z",
    },
}
```

`tests/test_index_cmd.py` — in `_route()`, add the field:

```python
def _route(store: Store, tmp_path: Path, item_id: str, topic: str) -> None:
    """Route the corpus item `item_id` to `topic` in the mirror."""
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    event: RoutingEvent = {
        "type": "route",
        "data": {
            "session": "s",
            "item_id": item_id,
            "verdict": topic,
            "note": "n",
            "item_updated_at": "2026-01-01T00:00:00Z",
        },
    }
    store.record_routing_event(routing, event)
```

`tests/test_cli_topic.py` — `_ROUTING_JSONL`:

```python
_ROUTING_JSONL = (
    '{"type": "route", "data": {"session": "s1", "item_id": "I_1", '
    '"verdict": "softwrap", "note": "note 1", '
    '"item_updated_at": "2026-01-01T00:00:00Z"}}\n'
    '{"type": "route", "data": {"session": "s1", "item_id": "I_2", '
    '"verdict": "softwrap", "note": "note 2", '
    '"item_updated_at": "2026-01-01T00:00:00Z"}}\n'
)
```

`tests/test_cli_close.py` — same `_ROUTING_JSONL` constant, same edit as `test_cli_topic.py` above.

`tests/test_triage.py` — the `last_event` equality assertion (~line 69) is checking what `record_verdict` itself produced, so derive the expected timestamp from the store rather than hardcoding it:

```python
    last_event = list(routing)[-1]
    assert last_event == {
        "type": "route",
        "data": {
            "session": token,
            "item_id": first_id,
            "verdict": "some-topic",
            "note": "reasoning",
            "item_updated_at": store.get_issue_or_pr(first).updated_at.isoformat(),
        },
    }
```

`tests/test_status.py` — the ghost-route JSONL fragment (item `I_ghost` doesn't exist in the fetch ledger, so any valid timestamp works):

```python
        + '{"type": "route", "data": {"session": "s1", "item_id": "I_ghost", '
        + '"verdict": "topic-a", "note": "routed after a corpus refetch", '
        + '"item_updated_at": "2026-01-01T00:00:00Z"}}\n'
```

`tests/fixtures/status_routing.jsonl` — full replacement:

```
{"type": "dispatched", "data": {"session": "s1", "item_id": "I_1", "packet_chars": 100, "fit": "fit", "diff_valve_dropped": 0}}
{"type": "route", "data": {"session": "s1", "item_id": "I_1", "verdict": "topic-a", "note": "relates to topic-a", "item_updated_at": "2026-01-01T00:00:00Z"}}
{"type": "dispatched", "data": {"session": "s1", "item_id": "I_2", "packet_chars": 80, "fit": "fit", "diff_valve_dropped": 0}}
{"type": "route", "data": {"session": "s1", "item_id": "I_2", "verdict": "no-signal", "note": "", "item_updated_at": "2026-01-01T00:00:00Z"}}
{"type": "dispatched", "data": {"session": "s1", "item_id": "I_3", "packet_chars": 120, "fit": "fit", "diff_valve_dropped": 0}}
{"type": "dispatched", "data": {"session": "s1", "item_id": "I_5", "packet_chars": 70, "fit": "fit", "diff_valve_dropped": 0}}
{"type": "route", "data": {"session": "s1", "item_id": "I_5", "verdict": "topic-b", "note": "a second real topic", "item_updated_at": "2026-01-01T00:00:00Z"}}
```

`tests/fixtures/routing_events.jsonl` — full replacement (pairs with `routing_fetch.jsonl`: `I_1` updatedAt `2026-01-01T00:00:00Z`, `I_2` updatedAt `2026-01-02T00:00:00Z`):

```
{"type": "dispatched", "data": {"session": "s1", "item_id": "I_1", "packet_chars": 100, "fit": "fit", "diff_valve_dropped": 0}}
{"type": "route", "data": {"session": "s1", "item_id": "I_1", "verdict": "softwrap", "note": "relates to softwrap", "item_updated_at": "2026-01-01T00:00:00Z"}}
{"type": "dispatched", "data": {"session": "s1", "item_id": "I_2", "packet_chars": 80, "fit": "fit", "diff_valve_dropped": 0}}
{"type": "route", "data": {"session": "s1", "item_id": "I_2", "verdict": "no-signal", "note": "", "item_updated_at": "2026-01-02T00:00:00Z"}}
{"type": "dispatched", "data": {"session": "s1", "item_id": "I_3", "packet_chars": 120, "fit": "fit", "diff_valve_dropped": 0}}
{"type": "route", "data": {"session": "s2", "item_id": "I_1", "verdict": "wrapping", "note": "actually about wrapping", "item_updated_at": "2026-01-01T00:00:00Z"}}
```

- [ ] **Step 7: Add a column-is-populated assertion**

In `tests/test_routing_replay.py::test_route_creates_topic_marked_dirty`, extend the assertion block to also check the stamp landed:

```python
def test_route_creates_topic_marked_dirty(store: Store, tmp_path: Path) -> None:
    """Routing an item to a new topic creates it, marked dirty, with a fresh stamp.

    When: item #1 is routed to "softwrap"
    Then: "softwrap" exists as a Topic and is marked dirty, and #1's
      Route carries the item_updated_at the fixture routed it with
    """
    with _session(tmp_path) as session:
        topic = session.get(Topic, "softwrap")
        route = session.get(Route, GraphQLId("I_1"))

    assert topic is not None
    assert topic.dirty is True
    assert route is not None
    assert route.item_updated_at.isoformat() == "2026-01-01T00:00:00+00:00"
```

(The stored value round-trips through `_ISODateTime` as `datetime.fromisoformat("2026-01-01T00:00:00Z").isoformat()`, which normalizes to the `+00:00` offset form — confirm this exact string by running the test once and reading the actual failure message if it differs.)

- [ ] **Step 8: Run the full suite, confirm green**

```bash
cd tools/ghmem && pytest -q
```

- [ ] **Step 9: Commit**

```bash
cd tools/ghmem && git add -A -- src/ghmem/ledger.py src/ghmem/models.py src/ghmem/ingest.py src/ghmem/triage.py tests/
git commit -m "feat(ghmem): stamp item_updated_at on every Route"
```

---

### Task 2: Reducer `"reenter"` branch

**Files:**
- Modify: `tools/ghmem/src/ghmem/ledger.py` (`ReenterData`, `ReenterEvent`, `RoutingEvent` union)
- Modify: `tools/ghmem/src/ghmem/ingest.py` (`_ROUTING_BY_TYPE`; new `"reenter"` branch)
- Create: `tools/ghmem/tests/test_reenter.py`

**Interfaces:**
- Consumes: `Route.item_updated_at` (Task 1), `Dispatch`, `Topic`, `_mark_topic_dirty` (existing).
- Produces: `ReenterEvent` (a `RoutingEvent` member other tasks and the CLI construct); the `"reenter"` reducer branch other tasks rely on being applied via `record_routing_event`.

- [ ] **Step 1: Write the failing tests**

Create `tools/ghmem/tests/test_reenter.py`:

```python
"""Test `reenter`: freeing a stale route's dispatch and dirtying its topic."""

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId
from ghmem.ledger import NO_SIGNAL, FetchEvent, Ledger, ReenterEvent, RoutingEvent
from ghmem.models import Dispatch, Route, Topic
from ghmem.store import Store

DB_NAME = "cache.sqlite"
FETCH_FIXTURE = Path(__file__).parent / "fixtures" / "routing_fetch.jsonl"

DISPATCH_1: RoutingEvent = {
    "type": "dispatched",
    "data": {
        "session": "s1",
        "item_id": "I_1",
        "packet_chars": 100,
        "fit": "fit",
        "diff_valve_dropped": 0,
    },
}
ROUTE_1: RoutingEvent = {
    "type": "route",
    "data": {
        "session": "s1",
        "item_id": "I_1",
        "verdict": "softwrap",
        "note": "relates to softwrap",
        "item_updated_at": "2026-01-01T00:00:00Z",
    },
}
DISPATCH_2: RoutingEvent = {
    "type": "dispatched",
    "data": {
        "session": "s1",
        "item_id": "I_2",
        "packet_chars": 80,
        "fit": "fit",
        "diff_valve_dropped": 0,
    },
}
ROUTE_NO_SIGNAL: RoutingEvent = {
    "type": "route",
    "data": {
        "session": "s1",
        "item_id": "I_2",
        "verdict": NO_SIGNAL,
        "note": "",
        "item_updated_at": "2026-01-02T00:00:00Z",
    },
}
REENTER_1: ReenterEvent = {"type": "reenter", "data": {"item_id": "I_1"}}
REENTER_2: ReenterEvent = {"type": "reenter", "data": {"item_id": "I_2"}}
REENTER_NEVER_ROUTED: ReenterEvent = {"type": "reenter", "data": {"item_id": "I_3"}}


def _live_store(tmp_path: Path) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a store seeded with the fetch fixture and an empty routing log."""
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


def _session(tmp_path: Path) -> Session:
    """Open a second session onto the same SQLite file the store uses."""
    return Session(create_engine(f"sqlite:///{tmp_path / DB_NAME}"))


def test_reenter_frees_the_dispatch_row(tmp_path: Path) -> None:
    """Reentering a routed item frees its outstanding Dispatch row.

    When: item #1 is dispatched and routed, then reentered
    Then: its Dispatch row is gone
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, DISPATCH_1)
    store.record_routing_event(routing, ROUTE_1)

    store.record_routing_event(routing, REENTER_1)

    with _session(tmp_path) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) is None


def test_reenter_dirties_the_routed_topic(tmp_path: Path) -> None:
    """Reentering a real-topic route dirties that topic again.

    When: item #1 is routed to "softwrap", the topic is marked clean,
      then #1 is reentered
    Then: "softwrap" is dirty again
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, DISPATCH_1)
    store.record_routing_event(routing, ROUTE_1)
    with _session(tmp_path) as session:
        session.get(Topic, "softwrap").dirty = False  # type: ignore[union-attr]
        session.commit()

    store.record_routing_event(routing, REENTER_1)

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)


def test_reenter_on_a_no_signal_route_touches_no_topic(tmp_path: Path) -> None:
    """Reentering a NO_SIGNAL route dirties nothing — there's no topic to dirty.

    When: item #2 is dispatched and routed to NO_SIGNAL, then reentered
    Then: no Topic named NO_SIGNAL exists, and its Dispatch is still freed
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, DISPATCH_2)
    store.record_routing_event(routing, ROUTE_NO_SIGNAL)

    store.record_routing_event(routing, REENTER_2)

    with _session(tmp_path) as session:
        assert session.get(Topic, NO_SIGNAL) is None
        assert session.get(Dispatch, GraphQLId("I_2")) is None


def test_reenter_with_no_dispatch_row_is_a_no_op(tmp_path: Path) -> None:
    """Reentering an item with no outstanding Dispatch raises nothing.

    Mirrors `requeue`'s idempotent-delete contract: a `reenter` for an
    item whose Dispatch is already gone (or was never dispatched) must
    replay cleanly, since a repeated or late-arriving event is expected,
    not corruption.

    When: item #3, never dispatched or routed, is reentered
    Then: recording it raises nothing, and a rebuild from the resulting
      ledger raises nothing either
    """
    store, routing = _live_store(tmp_path)

    store.record_routing_event(routing, REENTER_NEVER_ROUTED)

    with _session(tmp_path) as session:
        assert session.get(Dispatch, GraphQLId("I_3")) is None
    store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)


def test_live_reenter_then_reroute_matches_a_cold_rebuild(tmp_path: Path) -> None:
    """Recording reenter-then-reroute live matches replaying the ledger.

    When: item #1 is dispatched, routed, reentered, re-dispatched, and
      routed again — live — then the store is rebuilt from the
      resulting ledger
    Then: Route, Dispatch, and Topic rows are identical either way
    """
    store, routing = _live_store(tmp_path)
    reroute: RoutingEvent = {
        "type": "route",
        "data": {
            "session": "s2",
            "item_id": "I_1",
            "verdict": "wrapping",
            "note": "actually about wrapping",
            "item_updated_at": "2026-01-01T00:00:00Z",
        },
    }
    for event in (DISPATCH_1, ROUTE_1, REENTER_1, DISPATCH_1, reroute):
        store.record_routing_event(routing, event)

    with _session(tmp_path) as session:
        live = (
            session.get(Route, GraphQLId("I_1")),
            session.get(Dispatch, GraphQLId("I_1")),
            list(session.execute(select(Topic)).scalars()),
        )

    store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)

    with _session(tmp_path) as session:
        rebuilt = (
            session.get(Route, GraphQLId("I_1")),
            session.get(Dispatch, GraphQLId("I_1")),
            list(session.execute(select(Topic)).scalars()),
        )

    assert live[0] is not None
    assert live == rebuilt
```

- [ ] **Step 2: Run the new tests and confirm they fail**

```bash
cd tools/ghmem && pytest tests/test_reenter.py -v
```

Expected: every test fails — `UnknownRoutingEventError` for the unregistered `"reenter"` tag (or a `KeyError`/`ValidationError` depending on where it's caught).

- [ ] **Step 3: Add `ReenterData`/`ReenterEvent` and register the branch**

In `ledger.py`, after `CloseEvent` and before the `RoutingEvent` union:

```python
class ReenterData(TypedDict):
    """A routed item whose corpus activity moved past its verdict."""

    item_id: str


class ReenterEvent(TypedDict):
    """A ghmem-constructed record that a stale route was reopened."""

    type: Literal["reenter"]
    data: ReenterData


type RoutingEvent = (
    DispatchedEvent
    | RouteEvent
    | RequeueEvent
    | SnapshotEvent
    | CloseEvent
    | ReenterEvent
)
"""The routing-ledger event union."""
```

In `ingest.py`, add `ReenterEvent` to the `from ghmem.ledger import (...)` block, add `"reenter": ReenterEvent` to `_ROUTING_BY_TYPE`, and add the branch to `apply_routing_event` right after the `"close"` branch and before the closing `raise UnknownRoutingEventError(event_type)`:

```python
    if event["type"] == "reenter":
        data = event["data"]
        item_id = GraphQLId(data["item_id"])
        dispatch = session.get(Dispatch, item_id)
        if dispatch is not None:
            session.delete(dispatch)
        route = session.get(Route, item_id)
        if route is not None and route.verdict != NO_SIGNAL:
            _mark_topic_dirty(session, route.verdict)
        return
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
cd tools/ghmem && pytest tests/test_reenter.py -v
```

- [ ] **Step 5: Commit**

```bash
cd tools/ghmem && git add src/ghmem/ledger.py src/ghmem/ingest.py tests/test_reenter.py
git commit -m "feat(ghmem): add the reenter reducer branch"
```

---

### Task 3: Query change — `untriaged_heat_scores` freshness check

**Files:**
- Modify: `tools/ghmem/src/ghmem/heat_query.py:69-72`
- Modify: `tools/ghmem/tests/test_queue.py`

**Interfaces:**
- Consumes: `Route.item_updated_at` (Task 1).
- Produces: the corrected `untriaged_heat_scores` exclusion, which `serve_next` (existing) and the end-to-end test (Task 5) rely on.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_queue.py` (needs new imports: `from datetime import datetime`, and `IssueOrPR` added to the existing `from ghmem.models import Dispatch, HeatScore` line):

```python
def test_stale_routed_item_is_present_again(tmp_path: Path) -> None:
    """A Route stamped older than its item's current updated_at doesn't exclude.

    When: item #1 is routed with a stamp of "2026-01-01T00:00:00Z", item
      #2 is routed and left untouched, and #1's live updated_at then
      moves to "2026-01-03T00:00:00Z"
    Then: #1 rejoins the queue below #3 (fewer comments); #2 stays excluded
    """
    _store(
        tmp_path,
        RouteEvent(
            type="route",
            data={
                "session": "s1",
                "item_id": "I_1",
                "verdict": "softwrap",
                "note": "",
                "item_updated_at": "2026-01-01T00:00:00Z",
            },
        ),
        RouteEvent(
            type="route",
            data={
                "session": "s1",
                "item_id": "I_2",
                "verdict": "softwrap",
                "note": "",
                "item_updated_at": "2026-01-01T00:00:00Z",
            },
        ),
    )
    with Session(_engine(tmp_path)) as session:
        session.get_one(IssueOrPR, GraphQLId("I_1")).updated_at = datetime.fromisoformat(
            "2026-01-03T00:00:00Z"
        )
        session.commit()

    assert _numbers(tmp_path) == [3, 1]
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd tools/ghmem && pytest tests/test_queue.py::test_stale_routed_item_is_present_again -v
```

Expected: FAIL — `_numbers` returns `[3]`, since any `Route` still excludes unconditionally.

- [ ] **Step 3: Change the query**

In `heat_query.py`'s `untriaged_heat_scores`:

```python
def untriaged_heat_scores(
    engine: Engine, weights: Mapping[str, float]
) -> list[HeatScore]:
    """Score and rank every item not yet triaged, hottest first.

    "Not yet triaged" excludes an item carrying a *fresh* `Route` (its
    stamped `item_updated_at` still matches or leads the item's live
    `updated_at` — a real verdict that hasn't gone stale) or an
    outstanding `Dispatch` (some session is holding it right now), as
    two `NOT EXISTS` clauses on the same `item` alias `_ranked_query`
    builds its aggregate against. A stale Route no longer excludes —
    re-entry is what lets a corpus-changed item flow back through here.

    Neither needs the explicit `.correlate(item)` the subqueries in
    `_ranked_query` do: these are plain, non-compound `EXISTS`, which
    auto-correlation resolves to `item` on its own.
    """
    query, item = _ranked_query(weights)
    query = query.where(
        ~exists().where(
            Route.item_id == item.id,
            Route.item_updated_at >= item.updated_at,
        )
    ).where(~exists().where(Dispatch.item_id == item.id))
    return _run(engine, query, item)
```

- [ ] **Step 4: Run the full `test_queue.py` and confirm all pass**

```bash
cd tools/ghmem && pytest tests/test_queue.py -v
```

- [ ] **Step 5: Commit**

```bash
cd tools/ghmem && git add src/ghmem/heat_query.py tests/test_queue.py
git commit -m "feat(ghmem): untriaged_heat_scores only excludes a fresh Route"
```

---

### Task 4: `dispatch_store.stale_routes` + `Store` delegate + `cli.rebuild` wiring

**Files:**
- Modify: `tools/ghmem/src/ghmem/dispatch_store.py`
- Modify: `tools/ghmem/src/ghmem/store.py`
- Modify: `tools/ghmem/src/ghmem/cli.py:46-54`
- Modify: `tools/ghmem/tests/test_dispatch_store.py`
- Modify: `tools/ghmem/tests/test_cli_rebuild.py`

**Interfaces:**
- Consumes: `Route.item_updated_at` (Task 1), the `"reenter"` reducer branch (Task 2).
- Produces: `dispatch_store.stale_routes(engine) -> list[Route]`; `Store.stale_routes() -> list[Route]`; `ghmem rebuild`'s new reenter-and-report behavior, which Task 5's end-to-end test drives through the CLI.

**Design note to carry into the code's docstring** (this is *why* the query is shaped the way it is — not obvious from the spec alone, write it into `stale_routes`'s docstring verbatim-ish): the idempotency guard against re-flagging an already-stale item on every `rebuild` is "still carries an outstanding `Dispatch`". Right after a normal route, `Dispatch` is never deleted by the `"route"` branch and `Route.item_updated_at == item.updated_at` at that moment — never selected. Once new corpus activity makes it genuinely stale, `Dispatch` still exists (nothing else freed it) — selected once, `rebuild` reenters it, which frees `Dispatch`. On the next `rebuild`, before re-triage, `Dispatch` is absent — excluded, no duplicate event, no ledger re-scan or new mirror state needed. The one accepted gap: a `rebuild` running concurrently while a session is mid-re-triage of a freshly-reentered item (dispatch just re-reserved, no new verdict yet, no further corpus activity) would re-flag it — out of scope, matching the same operational assumption `requeue`'s own docstring already states: not expected to run concurrently with a live triage session.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dispatch_store.py` (needs new imports: `from datetime import datetime`, `IssueOrPR` added to `from ghmem.models import Dispatch`, and a `FETCH_FIXTURE = Path(__file__).parent / "fixtures" / "routing_fetch.jsonl"` module constant plus a small helper):

```python
FETCH_FIXTURE = Path(__file__).parent / "fixtures" / "routing_fetch.jsonl"


def _fetched_store(tmp_path: Path) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a store seeded with the fetch fixture and an empty routing log."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


def test_stale_routes_excludes_a_fresh_route(tmp_path: Path) -> None:
    """A Route stamped at its item's current updated_at is not stale.

    When: item #1 is dispatched and routed with item_updated_at equal to
      its live updated_at ("2026-01-01T00:00:00Z", per routing_fetch.jsonl)
    Then: stale_routes returns nothing
    """
    store, routing = _fetched_store(tmp_path)
    store.record_routing_event(
        routing,
        {
            "type": "dispatched",
            "data": {
                "session": "s1",
                "item_id": "I_1",
                "packet_chars": 100,
                "fit": "fit",
                "diff_valve_dropped": 0,
            },
        },
    )
    store.record_routing_event(
        routing,
        {
            "type": "route",
            "data": {
                "session": "s1",
                "item_id": "I_1",
                "verdict": "softwrap",
                "note": "",
                "item_updated_at": "2026-01-01T00:00:00Z",
            },
        },
    )

    assert store.stale_routes() == []


def test_stale_routes_includes_a_dispatched_stale_route(tmp_path: Path) -> None:
    """A Route stamped before its item's live updated_at, still dispatched, is stale.

    When: item #1 is dispatched and routed, then its live updated_at
      moves forward
    Then: stale_routes returns its Route
    """
    store, routing = _fetched_store(tmp_path)
    store.record_routing_event(
        routing,
        {
            "type": "dispatched",
            "data": {
                "session": "s1",
                "item_id": "I_1",
                "packet_chars": 100,
                "fit": "fit",
                "diff_valve_dropped": 0,
            },
        },
    )
    store.record_routing_event(
        routing,
        {
            "type": "route",
            "data": {
                "session": "s1",
                "item_id": "I_1",
                "verdict": "softwrap",
                "note": "",
                "item_updated_at": "2026-01-01T00:00:00Z",
            },
        },
    )
    with Session(create_engine(f"sqlite:///{tmp_path / 'cache.sqlite'}")) as session:
        session.get_one(IssueOrPR, GraphQLId("I_1")).updated_at = datetime.fromisoformat(
            "2026-01-05T00:00:00Z"
        )
        session.commit()

    stale = store.stale_routes()

    assert [r.item_id for r in stale] == [GraphQLId("I_1")]


def test_stale_routes_excludes_a_stale_route_already_freed(tmp_path: Path) -> None:
    """A stale Route with no outstanding Dispatch is not flagged again.

    The idempotency guard: once a reenter has already freed the
    Dispatch, the item must not be selected again until it's re-triaged
    and dispatched, or every rebuild before that would append another
    reenter event for it.

    When: item #1 is routed (never dispatched) and its live updated_at
      moves forward
    Then: stale_routes returns nothing
    """
    store, routing = _fetched_store(tmp_path)
    store.record_routing_event(
        routing,
        {
            "type": "route",
            "data": {
                "session": "s1",
                "item_id": "I_1",
                "verdict": "softwrap",
                "note": "",
                "item_updated_at": "2026-01-01T00:00:00Z",
            },
        },
    )
    with Session(create_engine(f"sqlite:///{tmp_path / 'cache.sqlite'}")) as session:
        session.get_one(IssueOrPR, GraphQLId("I_1")).updated_at = datetime.fromisoformat(
            "2026-01-05T00:00:00Z"
        )
        session.commit()

    assert store.stale_routes() == []
```

Add to `tests/test_cli_rebuild.py` (needs new imports: `from ghmem.aliases import GraphQLId`, `from ghmem.ledger import Ledger`, `from ghmem.models import Dispatch, Topic`, `from sqlalchemy import create_engine`, `from sqlalchemy.orm import Session`, plus `import pytest` if not already there):

```python
def _stale_route_corpus(tmp_path: Path) -> Path:
    """Write a corpus where item #1 is routed, then refetched with a later updatedAt."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(
        '{"type": "item", "data": {"id": "I_1", "__typename": "Issue", '
        '"number": 1, "title": "Item one", "state": "OPEN", '
        '"author": {"login": "alice"}, "createdAt": "2026-01-01T00:00:00Z", '
        '"updatedAt": "2026-01-01T00:00:00Z", "mergedBy": null, '
        '"mergedAt": null, "authorAssociation": "NONE", "body": "body one"}}\n'
        '{"type": "item", "data": {"id": "I_1", "__typename": "Issue", '
        '"number": 1, "title": "Item one", "state": "OPEN", '
        '"author": {"login": "alice"}, "createdAt": "2026-01-01T00:00:00Z", '
        '"updatedAt": "2026-01-05T00:00:00Z", "mergedBy": null, '
        '"mergedAt": null, "authorAssociation": "NONE", "body": "body one"}}\n',
        encoding="utf-8",
    )
    (corpus / "routing.jsonl").write_text(
        '{"type": "dispatched", "data": {"session": "s1", "item_id": "I_1", '
        '"packet_chars": 100, "fit": "fit", "diff_valve_dropped": 0}}\n'
        '{"type": "route", "data": {"session": "s1", "item_id": "I_1", '
        '"verdict": "softwrap", "note": "n", '
        '"item_updated_at": "2026-01-01T00:00:00Z"}}\n',
        encoding="utf-8",
    )
    return corpus


def test_rebuild_reenters_a_stale_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem rebuild` reenters an item whose corpus activity moved past its verdict.

    When: item #1 is dispatched, routed, then refetched with a later
      updatedAt (simulating a new comment's sync), and `ghmem rebuild`
      runs
    Then: its Dispatch row is freed, its topic is dirty, and the routing
      ledger gained a reenter event
    """
    corpus = _stale_route_corpus(tmp_path)
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))

    result = CliRunner().invoke(main, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert "reentered 1 item(s)" in result.output
    with Session(create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) is None
        assert session.get(Topic, "softwrap").dirty is True  # type: ignore[union-attr]
    reentered = [e for e in Ledger(corpus / "routing.jsonl") if e["type"] == "reenter"]
    assert reentered == [{"type": "reenter", "data": {"item_id": "I_1"}}]


def test_rebuild_does_not_reenter_an_already_freed_stale_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second `rebuild` does not reenter an item already reentered.

    When: `ghmem rebuild` runs twice in a row against the same
      still-stale item
    Then: the second run reenters 0 items — the first run already freed
      its Dispatch, and stale_routes' guard excludes it from then on
    """
    corpus = _stale_route_corpus(tmp_path)
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    first = CliRunner().invoke(main, ["rebuild"])
    assert first.exit_code == 0, first.output

    second = CliRunner().invoke(main, ["rebuild"])

    assert second.exit_code == 0, second.output
    assert "reentered 0 item(s)" in second.output
```

- [ ] **Step 2: Run the new tests and confirm they fail**

```bash
cd tools/ghmem && pytest tests/test_dispatch_store.py -k stale_routes -v
cd tools/ghmem && pytest tests/test_cli_rebuild.py -k reenter -v
```

Expected: `AttributeError: 'Store' object has no attribute 'stale_routes'`.

- [ ] **Step 3: Add `dispatch_store.stale_routes`**

```python
def stale_routes(engine: Engine) -> list[Route]:
    """Return every Route whose item has moved past its stamped verdict.

    A Route counts as stale when the item's live `updated_at` has moved
    past `Route.item_updated_at`, and it still carries an outstanding
    `Dispatch` — the second condition is the idempotency guard: a
    `"reenter"` event frees that `Dispatch`, so once one has already
    been appended for the current staleness episode, the item drops out
    of this query until it's re-triaged and dispatched again. Without
    it, every rebuild between staleness and re-triage would append
    another reenter event for the same item. Right after a normal
    route, `Dispatch` is never deleted by the `"route"` branch and the
    stamp equals the item's `updated_at`, so a freshly-routed item is
    never selected either way.
    """
    with Session(engine) as session:
        return list(
            session.execute(
                select(Route)
                .join(IssueOrPR, IssueOrPR.id == Route.item_id)
                .where(IssueOrPR.updated_at > Route.item_updated_at)
                .where(exists().where(Dispatch.item_id == Route.item_id))
                .order_by(Route.item_id)
            ).scalars()
        )
```

Place it after `dispatched_unrouted` (both `IssueOrPR` and `exists` are already imported in `dispatch_store.py`).

- [ ] **Step 4: Add the `Store` delegate**

In `store.py`, add `stale_routes` to the `from ghmem.dispatch_store import (...)` block (alphabetically, between `session_usage` and `topic_digest`), and add the delegate method after `dispatched_unrouted`:

```python
    def stale_routes(self) -> list[Route]:
        """Return every Route whose item has moved past its stamped verdict."""
        return stale_routes(self._engine)
```

- [ ] **Step 5: Wire `cli.rebuild`**

```python
@main.command()
def rebuild() -> None:
    """Rebuild the SQLite mirror, then reenter any item whose corpus moved past its verdict."""
    path = sqlite_path()
    store = Store.open(path)
    fetch: Ledger[FetchEvent] = Ledger(fetch_ledger_path())
    routing: Ledger[RoutingEvent] = Ledger(routing_ledger_path())
    store.rebuild_from_ledgers(fetch, routing)
    stale = store.stale_routes()
    for route in stale:
        event: RoutingEvent = {"type": "reenter", "data": {"item_id": route.item_id}}
        store.record_routing_event(routing, event)
    click.echo(f"Rebuilt {path}, reentered {len(stale)} item(s)")
```

- [ ] **Step 6: Run both test files and confirm green**

```bash
cd tools/ghmem && pytest tests/test_dispatch_store.py tests/test_cli_rebuild.py -v
```

- [ ] **Step 7: Commit**

```bash
cd tools/ghmem && git add src/ghmem/dispatch_store.py src/ghmem/store.py src/ghmem/cli.py tests/test_dispatch_store.py tests/test_cli_rebuild.py
git commit -m "feat(ghmem): detect and reenter stale routes on rebuild"
```

---

### Task 5: End-to-end lifecycle test

**Files:**
- Modify: `tools/ghmem/tests/test_triage.py` (or a same-directory helper import — use `triage_helpers._store`, `triage_helpers._engine`)

**Interfaces:**
- Consumes: everything from Tasks 1-4 — `Route.item_updated_at`, the `"reenter"` branch, the corrected `untriaged_heat_scores`, `Store.stale_routes`.
- Produces: nothing new — this is a pure integration check that the four prior tasks compose correctly, per the spec's "Data flow, end to end" section.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_triage.py` (needs `from datetime import datetime`, `from sqlalchemy.orm import Session`, `IssueOrPR` and `Route`, `Topic` added to whatever's already imported from `ghmem.models`, and `GraphQLId` from `ghmem.aliases` — check the file's existing import block and extend it rather than duplicating):

```python
def test_reenter_lifecycle_end_to_end(tmp_path: Path) -> None:
    """A stale route flows all the way back through triage to a fresh verdict.

    Exercises the spec's full "Data flow, end to end": route, corpus
    moves, stale_routes + reenter free it, it rejoins the untriaged
    queue, gets served again, and a fresh verdict closes the loop with
    an up-to-date stamp.

    When: item #1 is routed to "softwrap" (items #2 and #3 routed away
      too, so #1 is the only untriaged item), its live updated_at moves
      forward, stale_routes + a reenter event free it, and it's served
      and routed again
    Then: it carries a fresh item_updated_at, "softwrap" is dirty, and
      stale_routes reports nothing more for it
    """
    store, routing = _store(tmp_path)
    for other_id, verdict in (("I_2", "no-signal"), ("I_3", "other-topic")):
        store.record_routing_event(
            routing,
            {
                "type": "route",
                "data": {
                    "session": "s0",
                    "item_id": other_id,
                    "verdict": verdict,
                    "note": "n",
                    "item_updated_at": "2026-01-01T00:00:00Z",
                },
            },
        )
    store.record_routing_event(
        routing,
        {
            "type": "route",
            "data": {
                "session": "s0",
                "item_id": "I_1",
                "verdict": "softwrap",
                "note": "initial",
                "item_updated_at": "2026-01-01T00:00:00Z",
            },
        },
    )
    with Session(_engine(tmp_path)) as session:
        session.get_one(IssueOrPR, GraphQLId("I_1")).updated_at = datetime.fromisoformat(
            "2026-01-05T00:00:00Z"
        )
        session.commit()

    stale = store.stale_routes()
    assert [r.item_id for r in stale] == [GraphQLId("I_1")]
    for route in stale:
        store.record_routing_event(
            routing, {"type": "reenter", "data": {"item_id": route.item_id}}
        )

    token = triage.mint_session()
    served = triage.serve_next(store, routing, token)
    assert served.packet is not None
    assert "item: 1" in served.packet

    triage.record_verdict(
        store, routing, token, IssueOrPRNumber(1), "softwrap", "still softwrap"
    )

    with Session(_engine(tmp_path)) as session:
        route = session.get(Route, GraphQLId("I_1"))
        topic = session.get(Topic, "softwrap")
    assert route is not None
    assert route.item_updated_at == datetime.fromisoformat("2026-01-05T00:00:00Z")
    assert topic is not None
    assert topic.dirty is True
    assert store.stale_routes() == []
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd tools/ghmem && pytest tests/test_triage.py::test_reenter_lifecycle_end_to_end -v
```

Expected: FAIL at `store.stale_routes()` if any prior task were missing — but since Tasks 1-4 are already merged, this should actually mostly work already except for exact assertion values; run it once to confirm the *right* thing fails if anything does (e.g. a heat-ordering surprise), and adjust the routing of #2/#3 if `serve_next` doesn't land on #1 as expected. This step exists to catch integration gaps between the four prior tasks, not to prove any single line of new code.

- [ ] **Step 3: Fix whatever the run in Step 2 surfaces**

No new production code is anticipated here — if the test fails, it's either a wrong assertion value (fix the test) or a genuine integration gap between Tasks 1-4 (fix the relevant task's code, matching the design decisions already recorded in those tasks — do not improvise a new mechanism).

- [ ] **Step 4: Run the full suite and confirm all green**

```bash
cd tools/ghmem && pytest -q
```

- [ ] **Step 5: Commit**

```bash
cd tools/ghmem && git add tests/test_triage.py
git commit -m "test(ghmem): cover the reenter lifecycle end to end"
```

---

### Task 6: One-off `item_updated_at` backfill script

**Files:**
- Create: `tools/ghmem/src/ghmem/backfill_item_updated_at.py`
- Create: `tools/ghmem/tests/test_backfill_item_updated_at.py`

**Interfaces:**
- Consumes: `Store.routes()`, `Store.issue_or_pr_by_id()`, `Store.record_routing_event()` (all pre-existing), and `Route.item_updated_at` (Task 1, since this script is written and run last, after the column exists).
- Produces: nothing other tasks depend on — this is the live-corpus migration step, run once by hand.

Not a `ghmem` CLI command — no `@main.command()`, not registered in `cli.py`, not discoverable via `ghmem --help`. Runs via `python -m ghmem.backfill_item_updated_at`.

- [ ] **Step 1: Write the failing test**

Create `tools/ghmem/tests/test_backfill_item_updated_at.py`:

```python
"""Test the one-off `item_updated_at` backfill script."""

from datetime import datetime
from pathlib import Path

from ghmem.aliases import GraphQLId
from ghmem.backfill_item_updated_at import backfill
from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.models import Route
from ghmem.store import Store

DB_NAME = "cache.sqlite"
FETCH_FIXTURE = Path(__file__).parent / "fixtures" / "routing_fetch.jsonl"


def _live_store(tmp_path: Path) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a store seeded with the fetch fixture and an empty routing log."""
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


def test_backfill_stamps_every_routed_item_from_its_live_updated_at(
    tmp_path: Path,
) -> None:
    """Each routed item's corrected event carries its item's LIVE updated_at.

    The original route events below are deliberately stamped with an
    obviously wrong pre-column value, proving the backfill reads each
    item's current updated_at fresh rather than copying whatever the
    route already carried.

    When: items #1 and #2 are routed, each stamped wrong, and the
      backfill runs
    Then: it appends 2 events, and a rebuild from the resulting ledger
      leaves both Routes carrying their item's real live updated_at
      (2026-01-01 / 2026-01-02, per routing_fetch.jsonl) — verdict,
      note, and session unchanged
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(
        routing,
        {
            "type": "route",
            "data": {
                "session": "s1",
                "item_id": "I_1",
                "verdict": "softwrap",
                "note": "relates to softwrap",
                "item_updated_at": "2020-01-01T00:00:00Z",
            },
        },
    )
    store.record_routing_event(
        routing,
        {
            "type": "route",
            "data": {
                "session": "s1",
                "item_id": "I_2",
                "verdict": "no-signal",
                "note": "",
                "item_updated_at": "2020-01-01T00:00:00Z",
            },
        },
    )

    count = backfill(store, routing)

    assert count == 2
    store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)
    routes_by_item = {r.item_id: r for r in store.routes()}
    assert routes_by_item[GraphQLId("I_1")] == Route(
        item_id=GraphQLId("I_1"),
        verdict="softwrap",
        note="relates to softwrap",
        session="s1",
        item_updated_at=datetime.fromisoformat("2026-01-01T00:00:00Z"),
    )
    assert routes_by_item[GraphQLId("I_2")] == Route(
        item_id=GraphQLId("I_2"),
        verdict="no-signal",
        note="",
        session="s1",
        item_updated_at=datetime.fromisoformat("2026-01-02T00:00:00Z"),
    )
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd tools/ghmem && pytest tests/test_backfill_item_updated_at.py -v
```

Expected: `ModuleNotFoundError: No module named 'ghmem.backfill_item_updated_at'`.

- [ ] **Step 3: Write the script**

Create `tools/ghmem/src/ghmem/backfill_item_updated_at.py`:

```python
"""One-off backfill: stamp `item_updated_at` onto every routed item's Route.

Not a `ghmem` CLI command — a transition that happens exactly once, for a
tool with a user base of one, doesn't earn a maintained subcommand (see the
ghmem Part D design spec's Rollout section). Run once, by hand, against the
live corpus:

    python -m ghmem.backfill_item_updated_at

Appends one corrected `route` event per currently-routed item, carrying the
same session/verdict/note plus a freshly stamped `item_updated_at`. Every
appended event is mechanical — nothing about the verdict or note changes —
so, unlike the #1445/#3795/#3783 corrections, it needs no hand-verification.
"""

from ghmem.ledger import Ledger, RouteEvent, RoutingEvent
from ghmem.paths import routing_ledger_path, sqlite_path
from ghmem.store import Store


def backfill(store: Store, routing: Ledger[RoutingEvent]) -> int:
    """Append one corrected `route` event per currently-routed item.

    Returns the count appended.
    """
    routes = store.routes()
    for route in routes:
        item = store.issue_or_pr_by_id(route.item_id)
        assert item is not None, (
            "store.routes() already join-excludes items missing from the fetch ledger"
        )
        event: RouteEvent = {
            "type": "route",
            "data": {
                "session": route.session,
                "item_id": route.item_id,
                "verdict": route.verdict,
                "note": route.note,
                "item_updated_at": item.updated_at.isoformat(),
            },
        }
        store.record_routing_event(routing, event)
    return len(routes)


def main() -> None:
    """Run the backfill against the live corpus and report the count."""
    store = Store.open(sqlite_path())
    routing: Ledger[RoutingEvent] = Ledger(routing_ledger_path())
    count = backfill(store, routing)
    print(f"Backfilled item_updated_at for {count} route(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
cd tools/ghmem && pytest tests/test_backfill_item_updated_at.py -v
```

- [ ] **Step 5: Run the full suite**

```bash
cd tools/ghmem && pytest -q
```

- [ ] **Step 6: Commit**

```bash
cd tools/ghmem && git add src/ghmem/backfill_item_updated_at.py tests/test_backfill_item_updated_at.py
git commit -m "feat(ghmem): add the one-off item_updated_at backfill script"
```

- [ ] **Step 7 (operational, not a code change — run by hand, once, against the live corpus, before this plan's other 5 tasks are merged or at least before the next live `ghmem rebuild` runs against them):**

```bash
cd tools/ghmem && python -m ghmem.backfill_item_updated_at
```

Confirm the printed count matches the live corpus's routed-item count (55, as of the spec's writing — re-check with `ghmem status` if time has passed), then commit the resulting `corpus/routing.jsonl` change in the parent repo separately from this plan's own commits, since it's a data change, not a code change.

---

## Self-Review

**Spec coverage:** Ledger extension (Task 1) ✓. Rollout/backfill (Task 6) ✓. Query change (Task 3) ✓. Reducer branch (Task 2) ✓. Detection pass in `rebuild` (Task 4) ✓. Data flow end-to-end (Task 5) ✓. Concurrency/edge cases — no-signal re-entry (Task 2's `test_reenter_on_a_no_signal_route_touches_no_topic`), repeated-staleness-is-expected (Task 4's idempotency test + Task 5) ✓. Testing section's four mutation targets: query drops the freshness clause (Task 3's test would catch it — dropping the clause makes `test_stale_routed_item_is_present_again` fail), reducer skips the Dispatch delete (Task 2's `test_reenter_frees_the_dispatch_row`), reducer dirties unconditionally (Task 2's `test_reenter_on_a_no_signal_route_touches_no_topic`), backfill skips an item (caught by `NOT NULL` at the next rebuild, per Task 1's schema) ✓. Rebuild-equivalence test ✓ (Task 2's `test_live_reenter_then_reroute_matches_a_cold_rebuild`).

**Placeholder scan:** No TBD/TODO. Task 5 Step 2's note about "adjust if `serve_next` doesn't land on #1 as expected" is not a placeholder — it's an honest acknowledgment that an integration task's first run is itself the verification step, with a concrete instruction for what to do (fix the test or the task, never improvise).

**Type consistency:** `Route.item_updated_at: Mapped[datetime]` (Task 1) is read the same way in `stale_routes` (Task 4, `IssueOrPR.updated_at > Route.item_updated_at`) and `untriaged_heat_scores` (Task 3, `Route.item_updated_at >= item.updated_at`) — both compare `datetime` columns via SQLAlchemy, no type mismatch. `RouteEvent`/`RouteData["item_updated_at"]` is `str` (ISO 8601) everywhere it's constructed (Task 1's `record_verdict`, Task 6's `backfill`) and parsed via `datetime.fromisoformat` only in `ingest.py`'s reducer (Task 1) — consistent boundary. `ReenterEvent`/`ReenterData` (Task 2) is used identically in `cli.rebuild` (Task 4) and the backfill does not touch it at all (correct — backfill only ever emits `route` events).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-11-ghmem-d-reentry.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
