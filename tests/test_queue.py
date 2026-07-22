"""Test the untriaged queue: `untriaged_heat_scores`'s filter over heat.

Heat computation itself is `test_heat.py`'s contract; this file's
weights zero out every metric but `comments`, so the fixture's heat
order is trivial to reason about and every assertion here is about the
filter, not the score.
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId, IssueOrPRNumber
from ghmem.heat_query import untriaged_heat_scores
from ghmem.ledger import DispatchedEvent, FetchEvent, Ledger, RouteEvent, RoutingEvent
from ghmem.models import Dispatch, HeatScore
from ghmem.store import Store

DB_NAME = "cache.sqlite"
FIXTURE = Path(__file__).parent / "fixtures" / "queue_corpus.jsonl"
WEIGHTS = {
    "comments": 1.0,
    "reactions": 0.0,
    "participants": 0.0,
    "related_items": 0.0,
    "span_years": 0.0,
}


def _store(tmp_path: Path, *routing: RoutingEvent) -> Store:
    """Return a Store rebuilt from the queue fixture plus `routing`."""
    store = Store.open(tmp_path / DB_NAME)
    routing_ledger: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    for event in routing:
        routing_ledger.append(event)
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    store.rebuild_from_ledgers(fetch, routing_ledger)
    return store


def _engine(tmp_path: Path) -> Engine:
    """Return an engine on the mirror `_store` built under `tmp_path`."""
    return create_engine(f"sqlite:///{tmp_path / DB_NAME}")


def _numbers(tmp_path: Path) -> list[IssueOrPRNumber]:
    """Return the untriaged queue's item numbers, in ranked order."""
    scores: list[HeatScore] = untriaged_heat_scores(_engine(tmp_path), WEIGHTS)
    return [s.number for s in scores]


def test_unrouted_undispatched_corpus_returns_every_item_hottest_first(
    tmp_path: Path,
) -> None:
    """With no routing state at all, the whole corpus queues, by heat.

    When: three items carry 2, 1, and 3 comments respectively, and none
      of them has been dispatched or routed
    Then: the queue holds all three, hottest (#3) first
    """
    _store(tmp_path)

    assert _numbers(tmp_path) == [3, 1, 2]


def test_routed_item_is_absent(tmp_path: Path) -> None:
    """An item carrying a Route is excluded from the queue.

    Two items are routed, not one: a fixture with a single routed item
    cannot tell a per-item correlated exclusion from an uncorrelated
    one that treats "some Route row exists anywhere" as excluding
    every item, since both would leave the same single item behind.

    When: items #1 and #2 are routed, #3 is untouched
    Then: only #3 remains in the queue
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
            },
        ),
        RouteEvent(
            type="route",
            data={
                "session": "s1",
                "item_id": "I_2",
                "verdict": "softwrap",
                "note": "",
            },
        ),
    )

    assert _numbers(tmp_path) == [3]


def test_dispatched_unrouted_item_is_absent(tmp_path: Path) -> None:
    """An outstanding Dispatch with no Route excludes the item too.

    Two items are dispatched, not one, for the same reason as the
    Route case above.

    When: items #1 and #2 are dispatched and never routed, #3 is
      untouched
    Then: only #3 remains in the queue
    """
    _store(
        tmp_path,
        DispatchedEvent(
            type="dispatched",
            data={
                "session": "s1",
                "item_id": "I_1",
                "packet_chars": 100,
                "fit": "fit",
            },
        ),
        DispatchedEvent(
            type="dispatched",
            data={
                "session": "s1",
                "item_id": "I_2",
                "packet_chars": 100,
                "fit": "fit",
            },
        ),
    )

    assert _numbers(tmp_path) == [3]


def test_requeued_item_is_present_again(tmp_path: Path) -> None:
    """Deleting a Dispatch row (a requeue) returns the item to the queue.

    `RequeueEvent` doesn't exist yet (Task 5); a requeue is simulated
    here the way `test_routing_replay.py` simulates out-of-band mirror
    state, by deleting the row directly.

    When: items #1 and #2 are both dispatched, then #1's Dispatch row
      is deleted
    Then: #1 rejoins the queue below #3; #2 stays excluded
    """
    _store(
        tmp_path,
        DispatchedEvent(
            type="dispatched",
            data={
                "session": "s1",
                "item_id": "I_1",
                "packet_chars": 100,
                "fit": "fit",
            },
        ),
        DispatchedEvent(
            type="dispatched",
            data={
                "session": "s1",
                "item_id": "I_2",
                "packet_chars": 100,
                "fit": "fit",
            },
        ),
    )
    with Session(_engine(tmp_path)) as session:
        session.delete(session.get_one(Dispatch, GraphQLId("I_1")))
        session.commit()

    assert _numbers(tmp_path) == [3, 1]
