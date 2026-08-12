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
