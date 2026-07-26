"""Shared fixtures for the split topic test modules.

Not collected by pytest — the module carries no `test_` prefix. It is
imported by `test_topic` and `test_topic_no_signal`, mirroring
`triage_helpers`'s role for the split triage test modules.
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine

from ghmem.ledger import NO_SIGNAL, FetchEvent, Ledger, RouteEvent, RoutingEvent
from ghmem.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "heat_corpus.jsonl"
SHOW_FIXTURE = Path(__file__).parent / "fixtures" / "show_corpus.jsonl"


def _store(
    tmp_path: Path, fixture: Path = FIXTURE
) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a Store and its routing ledger, rebuilt from `fixture`."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(fixture)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


def _engine(tmp_path: Path) -> Engine:
    """Return a fresh engine on the mirror `_store` built under `tmp_path`."""
    return create_engine(f"sqlite:///{tmp_path / 'cache.sqlite'}")


def _route(
    store: Store, routing: Ledger[RoutingEvent], item_id: str, note: str
) -> None:
    """Route `item_id` to "softwrap" live, carrying `note`."""
    store.record_routing_event(
        routing,
        RouteEvent(
            type="route",
            data={
                "session": "s1",
                "item_id": item_id,
                "verdict": "softwrap",
                "note": note,
            },
        ),
    )


def _route_no_signal(store: Store, routing: Ledger[RoutingEvent], item_id: str) -> None:
    """Route `item_id` to `NO_SIGNAL` live, with the note every verdict carries.

    These tests write raw `RouteEvent`s directly, bypassing
    `record_verdict`'s guards, so the note is mirrored here rather than
    enforced — a `NO_SIGNAL` row with an empty note is a shape the
    ledger still holds from before the rule covered it, and no topic
    assembly should depend on which it gets.
    """
    store.record_routing_event(
        routing,
        RouteEvent(
            type="route",
            data={
                "session": "s1",
                "item_id": item_id,
                "verdict": NO_SIGNAL,
                "note": "nothing decided here",
            },
        ),
    )


def _token(output: str) -> str:
    """Extract the minted snapshot token from `assemble`'s first line."""
    return output.splitlines()[0].removeprefix("snapshot: ")
