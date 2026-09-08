"""Shared fixtures for the split routing test modules.

Not collected by pytest — the module carries no `test_` prefix. It is
imported by `test_routing_live`, `test_routing_replay`, `test_unroute`,
`test_reenter`, and `test_requeue`, mirroring `sync_helpers`'s role for
the split sync test modules.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.models import Topic
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


def _session(tmp_path: Path) -> Session:
    """Open a second session onto the same SQLite file the store uses."""
    return Session(create_engine(f"sqlite:///{tmp_path / DB_NAME}"))


@contextmanager
def _pinned_topics() -> Iterator[None]:
    """Hold every `Topic` the ORM loads, defeating the weak identity map.

    SQLAlchemy's identity map holds weak references, so a `Topic` no
    caller keeps is collected between two events of one replay and the
    next `session.get` re-reads it from the database. That masks any
    staleness a write leaves in the map. Holding the instances removes
    the accident, so a test can pin what the reducer guarantees rather
    than what garbage collection happens to repair.
    """
    held: list[Topic] = []

    def _hold(target: Topic, _context: object) -> None:
        held.append(target)

    event.listen(Topic, "load", _hold)
    try:
        yield
    finally:
        event.remove(Topic, "load", _hold)


def _dispatch(item_id: str) -> RoutingEvent:
    """Return a `dispatched` event handing `item_id` to session "s1"."""
    return {
        "type": "dispatched",
        "data": {
            "session": "s1",
            "item_id": item_id,
            "packet_chars": 100,
            "fit": "fit",
            "diff_valve_dropped": 0,
        },
    }


def _route(item_id: str, verdict: str, updated_at: str) -> RoutingEvent:
    """Return a `route` event sending `item_id` to `verdict`."""
    return {
        "type": "route",
        "data": {
            "session": "s1",
            "item_id": item_id,
            "verdict": verdict,
            "note": "a note",
            "item_updated_at": updated_at,
        },
    }
