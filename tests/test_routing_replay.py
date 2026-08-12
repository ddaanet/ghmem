"""Test the routing-ledger replay: `apply_routing_event` via rebuild.

The same reducer's live path — one event appended and applied, rather
than a whole ledger replayed — is covered by `test_routing_live.py`.
"""

from datetime import datetime
from pathlib import Path

import msgspec
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId
from ghmem.exceptions import InvalidItemUpdatedAtError, UnknownRoutingEventError
from ghmem.ledger import NO_SIGNAL, FetchEvent, Ledger, RoutingEvent
from ghmem.models import Dispatch, Route, Topic
from ghmem.store import Store

DB_NAME = "cache.sqlite"
FETCH_FIXTURE = Path(__file__).parent / "fixtures" / "routing_fetch.jsonl"
ROUTING_FIXTURE = Path(__file__).parent / "fixtures" / "routing_events.jsonl"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from the routing fixture ledgers."""
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(ROUTING_FIXTURE)
    store.rebuild_from_ledgers(fetch, routing)
    return store


def _session(tmp_path: Path) -> Session:
    """Open a second session onto the same SQLite file the store uses.

    `Store` doesn't expose `Route`/`Dispatch`/`Topic` queries yet (that's
    Task 4) — this reopens the same on-disk database directly so tests
    can assert via plain `select`/`session.get`.
    """
    return Session(create_engine(f"sqlite:///{tmp_path / DB_NAME}"))


def test_route_creates_topic_marked_dirty(store: Store, tmp_path: Path) -> None:
    """Routing an item to a new topic creates it, marked dirty.

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


def test_no_signal_routes_but_creates_no_topic(store: Store, tmp_path: Path) -> None:
    """Routing to NO_SIGNAL records a verdict but touches no topic.

    When: item #2 is routed to NO_SIGNAL
    Then: its whole Route row matches the ledger event, and no Topic
      named NO_SIGNAL exists
    """
    with _session(tmp_path) as session:
        route = session.get(Route, GraphQLId("I_2"))
        topic = session.get(Topic, NO_SIGNAL)

    assert route == Route(
        item_id=GraphQLId("I_2"),
        verdict=NO_SIGNAL,
        note="",
        session="s1",
        item_updated_at=datetime.fromisoformat("2026-01-02T00:00:00Z"),
    )
    assert topic is None


def test_reroute_replaces_the_verdict_and_dirties_both_topics(
    store: Store, tmp_path: Path
) -> None:
    """A re-route is latest-wins, and leaves both topics dirty.

    The *old*-topic half of the reducer is NOT proven here, and cannot
    be from a rebuild: replay starts from a dropped schema, so the
    topic an item leaves was necessarily created dirty by the route
    that first put it there. Deleting the reducer's old-topic dirtying
    keeps this test green — verified by mutation. Only a path that can
    clean a topic between two events (Part C's synthesis, or the live
    apply seam) can distinguish the two, so the assertion below is
    deliberately the weaker "both are dirty", not evidence of why.

    When: item #1 routes to "softwrap", then re-routes to "wrapping"
    Then: its whole Route row is the second event's, and both topics
      exist and are dirty
    """
    with _session(tmp_path) as session:
        softwrap = session.get(Topic, "softwrap")
        wrapping = session.get(Topic, "wrapping")
        route = session.get(Route, GraphQLId("I_1"))

    assert softwrap == Topic(name="softwrap", dirty=True)
    assert wrapping == Topic(name="wrapping", dirty=True)
    assert route == Route(
        item_id=GraphQLId("I_1"),
        verdict="wrapping",
        note="actually about wrapping",
        session="s2",
        item_updated_at=datetime.fromisoformat("2026-01-01T00:00:00Z"),
    )


def test_dispatched_without_route_is_stranded(store: Store, tmp_path: Path) -> None:
    """A dispatched item with no route is a stranded dispatch.

    When: item #3 is dispatched but never routed
    Then: its whole Dispatch row matches the ledger event, and no Route
      row exists
    """
    with _session(tmp_path) as session:
        dispatch = session.get(Dispatch, GraphQLId("I_3"))
        route = session.get(Route, GraphQLId("I_3"))

    assert dispatch == Dispatch(
        item_id=GraphQLId("I_3"), session="s1", packet_chars=120
    )
    assert route is None


def test_every_dispatch_carries_its_own_packet_size(
    store: Store, tmp_path: Path
) -> None:
    """Each dispatch keeps the packet size it was served with.

    `packet_chars` is what Part B meters session budgets against, so a
    reducer that dropped or flattened it would silently unmeter every
    budget. The three fixture dispatches carry distinct sizes precisely
    so a constant cannot satisfy this.

    When: three items are dispatched with 100, 80, and 120 chars
    Then: each Dispatch row carries its own size
    """
    with _session(tmp_path) as session:
        sizes = {
            d.item_id: d.packet_chars
            for d in session.execute(select(Dispatch)).scalars()
        }

    assert sizes == {"I_1": 100, "I_2": 80, "I_3": 120}


def test_unhandled_event_kind_is_rejected_not_routed(tmp_path: Path) -> None:
    """A routing event kind with no reducer branch stops the replay.

    The `RoutingEvent` union grows across parts, and every new member
    has to extend the reducer. An unmistakably-unknown tag with a
    `route`-shaped payload must not fall through into the `route`
    branch and invent a Route and a dirty Topic out of a topic name.

    When: the routing ledger carries a `route`-shaped event tagged
      with a kind no member declares
    Then: rebuild raises `UnknownRoutingEventError`, and no Route row
      is created for it
    """
    routing_path = tmp_path / "off_union_routing.jsonl"
    routing_path.write_text(
        '{"type": "bogus", "data": {"session": "s1", "item_id": "I_1", '
        '"verdict": "softwrap", "note": ""}}\n',
        encoding="utf-8",
    )
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(routing_path)

    with pytest.raises(UnknownRoutingEventError):
        store.rebuild_from_ledgers(fetch, routing)

    with _session(tmp_path) as session:
        assert session.get(Route, GraphQLId("I_1")) is None


def test_dispatched_event_missing_a_field_is_rejected(tmp_path: Path) -> None:
    """An event short of a declared field is rejected, not quietly replayed.

    `fit` and `diff_valve_dropped` are write-only: no reducer branch
    reads them, and `Ledger.__iter__` hands out a `RoutingEvent` on
    the strength of a `cast`. Without `validate_routing_event` an
    event missing either one replays perfectly and is found missing
    only by whichever consumer first wants it — and `routing.jsonl` is
    the one ledger that cannot be refetched.

    When: a dispatched event carrying every field but `fit` is
      replayed
    Then: rebuild raises `msgspec.ValidationError`, and no Dispatch
      row is created for it
    """
    routing_path = tmp_path / "short_routing.jsonl"
    routing_path.write_text(
        '{"type": "dispatched", "data": {"session": "s1", "item_id": "I_1", '
        '"packet_chars": 100, "diff_valve_dropped": 0}}\n',
        encoding="utf-8",
    )
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(routing_path)

    with pytest.raises(msgspec.ValidationError):
        store.rebuild_from_ledgers(fetch, routing)

    with _session(tmp_path) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) is None


def test_rebuild_discards_state_absent_from_the_ledgers(tmp_path: Path) -> None:
    """A rebuild removes mirror rows no ledger event accounts for.

    This is what makes the SQLite file disposable: it is a cache of the
    ledgers, so anything in it the ledgers do not produce must not
    survive a rebuild. Every write is an upsert by id, so idempotence
    alone does not prove it — only a row that ought to VANISH does.
    Deleting the `drop_all` call leaves `test_rebuild_is_idempotent`
    green; it does not leave this one green.

    When: a Topic and a Route with no ledger event behind them are
      inserted straight into the mirror, and the store is rebuilt
    Then: both are gone, and the ledger-backed rows remain
    """
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(ROUTING_FIXTURE)
    store.rebuild_from_ledgers(fetch, routing)
    with _session(tmp_path) as session:
        session.add(Topic(name="never-routed", dirty=True))
        session.add(
            Route(
                item_id=GraphQLId("I_9"),
                verdict="ghost",
                note="",
                session="stale",
                item_updated_at=datetime.fromisoformat("2026-01-01T00:00:00Z"),
            )
        )
        session.commit()

    store.rebuild_from_ledgers(fetch, routing)

    with _session(tmp_path) as session:
        assert session.get(Topic, "never-routed") is None
        assert session.get(Route, GraphQLId("I_9")) is None
        assert session.get(Topic, "wrapping") is not None


def test_route_with_naive_item_updated_at_is_rejected(tmp_path: Path) -> None:
    """A route event's naive `item_updated_at` is rejected, not stored.

    A hand-appended `route` event with a naive (no-timezone) timestamp
    — the exact shape a hand-writer might produce, per the design
    spec's Motivation section — must not reach `Route.item_updated_at`,
    where it would silently break `stale_routes`' textual SQL
    comparison and raise an uncaught `TypeError` from
    `record_verdict`'s Python comparison.

    When: the routing ledger carries a `route` event whose
      `item_updated_at` has no timezone
    Then: rebuild raises `InvalidItemUpdatedAtError` naming the item,
      and no Route row is created for it
    """
    routing_path = tmp_path / "naive_routing.jsonl"
    routing_path.write_text(
        '{"type": "route", "data": {"session": "s1", "item_id": "I_1", '
        '"verdict": "softwrap", "note": "n", '
        '"item_updated_at": "2026-01-01T00:00:00"}}\n',
        encoding="utf-8",
    )
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(routing_path)

    with pytest.raises(InvalidItemUpdatedAtError) as excinfo:
        store.rebuild_from_ledgers(fetch, routing)
    assert excinfo.value.item_id == "I_1"

    with _session(tmp_path) as session:
        assert session.get(Route, GraphQLId("I_1")) is None


def test_route_with_unparseable_item_updated_at_is_rejected(tmp_path: Path) -> None:
    """A route event's unparseable `item_updated_at` is rejected the same way.

    When: the routing ledger carries a `route` event whose
      `item_updated_at` is not a valid ISO-8601 string at all
    Then: rebuild raises `InvalidItemUpdatedAtError` naming the item,
      and no Route row is created for it
    """
    routing_path = tmp_path / "malformed_routing.jsonl"
    routing_path.write_text(
        '{"type": "route", "data": {"session": "s1", "item_id": "I_1", '
        '"verdict": "softwrap", "note": "n", '
        '"item_updated_at": "not-a-timestamp"}}\n',
        encoding="utf-8",
    )
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(routing_path)

    with pytest.raises(InvalidItemUpdatedAtError) as excinfo:
        store.rebuild_from_ledgers(fetch, routing)
    assert excinfo.value.item_id == "I_1"

    with _session(tmp_path) as session:
        assert session.get(Route, GraphQLId("I_1")) is None


def test_rebuild_is_idempotent(tmp_path: Path) -> None:
    """Rebuilding twice from the same ledgers yields identical rows.

    When: the store is rebuilt twice in a row from the routing fixtures
    Then: querying afterwards returns one row per id (not duplicates),
      with every routed topic still dirty
    """
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(ROUTING_FIXTURE)
    store.rebuild_from_ledgers(fetch, routing)
    store.rebuild_from_ledgers(fetch, routing)

    with _session(tmp_path) as session:
        routes = list(session.execute(select(Route)).scalars())
        dispatches = list(session.execute(select(Dispatch)).scalars())
        topics = list(session.execute(select(Topic)).scalars())

    assert len(routes) == 2  # #1 (wrapping) and #2 (no-signal)
    assert len(dispatches) == 3
    assert {t.name for t in topics} == {"softwrap", "wrapping"}
    assert all(t.dirty for t in topics)
