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
