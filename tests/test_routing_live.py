"""Test the live routing write path: append to the ledger, then apply."""

from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId
from ghmem.exceptions import UnknownRoutingEventError
from ghmem.ledger import FetchEvent, Ledger, RouteEvent, RoutingEvent
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
ROUTE_SOFTWRAP: RouteEvent = {
    "type": "route",
    "data": {
        "session": "s1",
        "item_id": "I_1",
        "verdict": "softwrap",
        "note": "relates to softwrap",
    },
}
ROUTE_WRAPPING: RouteEvent = {
    "type": "route",
    "data": {
        "session": "s2",
        "item_id": "I_1",
        "verdict": "wrapping",
        "note": "actually about wrapping",
    },
}


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


def test_a_rejected_event_never_reaches_the_ledger(tmp_path: Path) -> None:
    """An event the reducer refuses leaves the routing ledger empty.

    `routing.jsonl` is the one file here that cannot be regenerated, and
    replay is strict — so an event the reducer rejects, once written,
    makes every future `rebuild` raise until someone edits the ledger by
    hand.

    This is the same parse-before-append rule the fetch driver already
    follows via `validate_raw_node`. The routing ledger needs it more,
    not less: the fetch ledger can simply be refetched.

    When: an event carrying a tag with no reducer branch is recorded
    Then: `UnknownRoutingEventError` propagates, the ledger is still
      empty, and a rebuild from it still works
    """
    store, routing = _live_store(tmp_path)
    off_union = cast(
        "RoutingEvent",
        {"type": "close", "data": {"session": "s1", "item_id": "I_1"}},
    )

    with pytest.raises(UnknownRoutingEventError):
        store.record_routing_event(routing, off_union)

    assert list(routing) == []
    store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)


def test_recording_writes_the_ledger_and_the_mirror(tmp_path: Path) -> None:
    """One call both appends the event and applies it.

    When: a dispatch and a route are recorded
    Then: both events are in the routing ledger in order, and both
      rows are queryable from the mirror
    """
    store, routing = _live_store(tmp_path)

    store.record_routing_event(routing, DISPATCH_1)
    store.record_routing_event(routing, ROUTE_SOFTWRAP)

    assert list(routing) == [DISPATCH_1, ROUTE_SOFTWRAP]
    with _session(tmp_path) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) == Dispatch(
            item_id=GraphQLId("I_1"), session="s1", packet_chars=100
        )
        assert session.get(Route, GraphQLId("I_1")) == Route(
            item_id=GraphQLId("I_1"),
            verdict="softwrap",
            note="relates to softwrap",
            session="s1",
        )


def test_live_writes_match_a_cold_rebuild(tmp_path: Path) -> None:
    """Recording live leaves exactly what replaying the ledger would.

    This is the claim the two-ledger architecture rests on: the live
    command path and `rebuild_from_ledgers` share one reducer, so they
    cannot drift. Rebuilding from the ledger the live path just wrote
    must be a no-op on the observable rows.

    When: three events are recorded live, then the store is rebuilt
      from the ledger those writes produced
    Then: routes, dispatches, and topics are identical either way —
      and non-empty, since two empty mirrors also compare equal
    """
    store, routing = _live_store(tmp_path)
    for event in (DISPATCH_1, ROUTE_SOFTWRAP, ROUTE_WRAPPING):
        store.record_routing_event(routing, event)

    with _session(tmp_path) as session:
        live = (
            list(session.execute(select(Route)).scalars()),
            list(session.execute(select(Dispatch)).scalars()),
            list(session.execute(select(Topic)).scalars()),
        )

    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    store.rebuild_from_ledgers(fetch, routing)

    with _session(tmp_path) as session:
        rebuilt = (
            list(session.execute(select(Route)).scalars()),
            list(session.execute(select(Dispatch)).scalars()),
            list(session.execute(select(Topic)).scalars()),
        )

    routes, dispatches, topics = live
    assert len(routes) == 1
    assert len(dispatches) == 1
    assert {t.name for t in topics} == {"softwrap", "wrapping"}
    assert live == rebuilt


def test_reroute_dirties_the_topic_it_leaves(tmp_path: Path) -> None:
    """Re-routing away from a clean topic dirties it again.

    Only reachable live: a rebuild starts from a dropped schema, so the
    topic an item leaves was always created dirty by the route that put
    it there, and dropping the reducer's old-topic dirtying changes
    nothing observable. Cleaning "softwrap" here stands in for the
    synthesis that will clear it in Part C.

    When: item #1 is routed to "softwrap", that topic is marked clean,
      and #1 is then re-routed to "wrapping"
    Then: "softwrap" is dirty again — it needs re-synthesis without the
      item that had been its content
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, ROUTE_SOFTWRAP)
    with _session(tmp_path) as session:
        session.get(Topic, "softwrap").dirty = False  # type: ignore[union-attr]
        session.commit()

    store.record_routing_event(routing, ROUTE_WRAPPING)

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)
        assert session.get(Topic, "wrapping") == Topic(name="wrapping", dirty=True)
