"""Test `stale_routes`: detecting a verdict the corpus has moved past."""

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId
from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.models import IssueOrPR
from ghmem.store import Store

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
    """A stale, still-dispatched Route: stamped before the item's updated_at.

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
        session.get_one(
            IssueOrPR, GraphQLId("I_1")
        ).updated_at = datetime.fromisoformat("2026-01-05T00:00:00Z")
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
        session.get_one(
            IssueOrPR, GraphQLId("I_1")
        ).updated_at = datetime.fromisoformat("2026-01-05T00:00:00Z")
        session.commit()

    assert store.stale_routes() == []
