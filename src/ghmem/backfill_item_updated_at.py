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
