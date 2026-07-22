"""Test Store's triage-session metering and race-free reservation."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId
from ghmem.ledger import DispatchedEvent, FetchEvent, Ledger, RoutingEvent
from ghmem.models import Dispatch
from ghmem.store import Store


def _empty_store(tmp_path: Path) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a store with the schema created but no items or dispatches.

    `Dispatch` carries no foreign key to `IssueOrPR`, so metering and
    reservation tests need no fetched items — just the schema, which an
    empty-ledger rebuild creates without seeding any rows.
    """
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


@contextmanager
def _recorded_statements() -> Iterator[list[str]]:
    """Record every SQL statement executed on any engine in the block."""
    statements: list[str] = []

    def record(  # noqa: ANN202
        _conn: Any,  # noqa: ANN401
        _cursor: Any,  # noqa: ANN401
        statement: str,
        _params: Any,  # noqa: ANN401
        _context: Any,  # noqa: ANN401
        _executemany: bool,  # noqa: FBT001
    ):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def test_session_usage_sums_chars_and_counts_items_for_one_token(
    tmp_path: Path,
) -> None:
    """session_usage aggregates one session's dispatches, not another's.

    When: two items are dispatched to "s1" (with distinct packet_chars,
      so a token/count mix-up can't pass by coincidence) and one item is
      dispatched to "s2"
    Then: session_usage("s1") counts only its two items and sums only
      their chars, ignoring "s2"'s row entirely
    """
    store, routing = _empty_store(tmp_path)
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
            "type": "dispatched",
            "data": {
                "session": "s1",
                "item_id": "I_2",
                "packet_chars": 250,
                "fit": "fit",
                "diff_valve_dropped": 0,
            },
        },
    )
    store.record_routing_event(
        routing,
        {
            "type": "dispatched",
            "data": {
                "session": "s2",
                "item_id": "I_3",
                "packet_chars": 999,
                "fit": "fit",
                "diff_valve_dropped": 0,
            },
        },
    )

    assert store.session_usage("s1") == (2, 350)
    assert store.session_usage("s2") == (1, 999)


def test_session_usage_unknown_token_returns_zero(tmp_path: Path) -> None:
    """session_usage on a token with no dispatches returns (0, 0).

    When: session_usage is called for a token that never dispatched
      anything
    Then: it returns (0, 0) rather than raising or returning None
    """
    store, _routing = _empty_store(tmp_path)

    assert store.session_usage("nobody") == (0, 0)


def test_reserve_dispatch_on_free_item_succeeds(tmp_path: Path) -> None:
    """Reserving a free item succeeds, logs once, and writes one row.

    When: reserve_dispatch is called for an item with no existing
      Dispatch row
    Then: it returns True, the event lands in the routing ledger, and
      exactly one Dispatch row exists for that item
    """
    store, routing = _empty_store(tmp_path)
    event: DispatchedEvent = {
        "type": "dispatched",
        "data": {
            "session": "s1",
            "item_id": "I_1",
            "packet_chars": 100,
            "fit": "fit",
            "diff_valve_dropped": 0,
        },
    }

    assert store.reserve_dispatch(routing, event) is True

    assert list(routing) == [event]
    with Session(create_engine(f"sqlite:///{tmp_path / 'cache.sqlite'}")) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) == Dispatch(
            item_id=GraphQLId("I_1"), session="s1", packet_chars=100
        )


def test_reserve_dispatch_emits_a_single_conditional_insert(tmp_path: Path) -> None:
    """The write is one statement, not a check followed by a write.

    A read-then-write reservation passes every outcome assertion in this
    file — both statements individually succeed — so the only way to
    prove `reserve_dispatch` never opens that window is to look at what
    actually reaches the database: exactly one statement against
    `dispatch`, carrying `ON CONFLICT` so a losing writer no-ops instead
    of racing a separate `SELECT`.

    When: reserve_dispatch is called once, on a free item
    Then: exactly one statement executed against the `dispatch` table,
      and it contains "ON CONFLICT"
    """
    store, routing = _empty_store(tmp_path)
    event: DispatchedEvent = {
        "type": "dispatched",
        "data": {
            "session": "s1",
            "item_id": "I_1",
            "packet_chars": 100,
            "fit": "fit",
            "diff_valve_dropped": 0,
        },
    }

    with _recorded_statements() as statements:
        store.reserve_dispatch(routing, event)

    dispatch_statements = [s for s in statements if "dispatch" in s.lower()]
    assert len(dispatch_statements) == 1, dispatch_statements
    assert "ON CONFLICT" in dispatch_statements[0]


def test_reserve_dispatch_declines_an_already_taken_item(tmp_path: Path) -> None:
    """A second reservation of an already-dispatched item is declined.

    This checks the *outcome* the race must produce, not the atomicity
    that produces it: `reserve_dispatch` opens and commits its own
    `Session` per call, so two sequential calls are fully serialized, and
    the second item would read as taken under any implementation,
    race-free or not.
    `test_reserve_dispatch_emits_a_single_conditional_insert` is what
    proves the mechanism — a single conditional insert, never a
    read-then-write — that actually forecloses the race.

    When: "s1" reserves item "I_1", then "s2" reserves the same item
    Then: the second call returns False, appends nothing to the ledger,
      and leaves the first reservation's session and packet_chars
      untouched
    """
    store, routing = _empty_store(tmp_path)
    first: DispatchedEvent = {
        "type": "dispatched",
        "data": {
            "session": "s1",
            "item_id": "I_1",
            "packet_chars": 100,
            "fit": "fit",
            "diff_valve_dropped": 0,
        },
    }
    second: DispatchedEvent = {
        "type": "dispatched",
        "data": {
            "session": "s2",
            "item_id": "I_1",
            "packet_chars": 999,
            "fit": "fit",
            "diff_valve_dropped": 0,
        },
    }

    assert store.reserve_dispatch(routing, first) is True
    assert store.reserve_dispatch(routing, second) is False

    assert list(routing) == [first]
    with Session(create_engine(f"sqlite:///{tmp_path / 'cache.sqlite'}")) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) == Dispatch(
            item_id=GraphQLId("I_1"), session="s1", packet_chars=100
        )


def test_reserve_dispatch_write_matches_a_cold_rebuild(tmp_path: Path) -> None:
    """A reservation written live is what replaying the ledger produces.

    Mirrors `test_routing_live.test_live_writes_match_a_cold_rebuild` for
    `reserve_dispatch`'s second write path. There is no drift today, but
    nothing enforces that either — this is the regression guard for a
    future `Dispatch` column added to the reducer and missed here, or the
    other way around.

    When: an item is reserved live, then the store is rebuilt from the
      ledger that write produced
    Then: the `Dispatch` row is identical either way
    """
    store, routing = _empty_store(tmp_path)
    event: DispatchedEvent = {
        "type": "dispatched",
        "data": {
            "session": "s1",
            "item_id": "I_1",
            "packet_chars": 100,
            "fit": "fit",
            "diff_valve_dropped": 0,
        },
    }
    store.reserve_dispatch(routing, event)
    engine = create_engine(f"sqlite:///{tmp_path / 'cache.sqlite'}")
    with Session(engine) as session:
        live = session.get(Dispatch, GraphQLId("I_1"))

    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    store.rebuild_from_ledgers(fetch, routing)

    with Session(engine) as session:
        rebuilt = session.get(Dispatch, GraphQLId("I_1"))

    assert live is not None
    assert live == rebuilt


def test_dispatched_unrouted_excludes_routed(tmp_path: Path) -> None:
    """dispatched_unrouted excludes items that later got routed.

    When: #1, #2, and #3 are all dispatched, but only #3 has no Route
    Then: dispatched_unrouted returns only #3
    """
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(
        Path(__file__).parent / "fixtures" / "routing_fetch.jsonl"
    )
    routing: Ledger[RoutingEvent] = Ledger(
        Path(__file__).parent / "fixtures" / "routing_events.jsonl"
    )
    store.rebuild_from_ledgers(fetch, routing)

    unrouted = store.dispatched_unrouted()

    assert [d.item_id for d in unrouted] == [GraphQLId("I_3")]
