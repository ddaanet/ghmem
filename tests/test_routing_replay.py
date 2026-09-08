"""Test the routing-ledger replay: `apply_routing_event` via rebuild.

The same reducer's live path — one event appended and applied, rather
than a whole ledger replayed — is covered by `test_routing_live.py`.
"""

from datetime import datetime
from pathlib import Path
from typing import cast

import msgspec
import pytest
from sqlalchemy import select

from ghmem.aliases import GraphQLId
from ghmem.exceptions import InvalidItemUpdatedAtError, UnknownRoutingEventError
from ghmem.ledger import NO_SIGNAL, FetchEvent, Ledger, RoutingEvent
from ghmem.models import Dispatch, Route, Topic
from ghmem.store import Store
from tests.routing_helpers import (
    DB_NAME,
    FETCH_FIXTURE,
    _pinned_topics,
    _route,
    _session,
)

ROUTING_FIXTURE = Path(__file__).parent / "fixtures" / "routing_events.jsonl"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from the routing fixture ledgers."""
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(ROUTING_FIXTURE)
    store.rebuild_from_ledgers(fetch, routing)
    return store


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


def test_reroute_replaces_the_verdict_and_drops_the_emptied_topic(
    store: Store, tmp_path: Path
) -> None:
    """A re-route is latest-wins, and drops the topic it empties.

    When: item #1 routes to "softwrap", then re-routes to "wrapping"
    Then: its whole Route row is the second event's, "softwrap" no
      longer exists, and "wrapping" exists and is dirty
    """
    with _session(tmp_path) as session:
        softwrap = session.get(Topic, "softwrap")
        wrapping = session.get(Topic, "wrapping")
        route = session.get(Route, GraphQLId("I_1"))

    assert softwrap is None
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
    assert {t.name for t in topics} == {"wrapping"}
    assert all(t.dirty for t in topics)


def test_a_topic_emptied_and_reminted_in_one_replay_ends_up_dirty(
    tmp_path: Path,
) -> None:
    """A topic emptied and re-minted inside one replay is present, dirty.

    Builds its own ledger rather than the shared fixture, which
    `test_requeue.py` and `test_dispatch_store.py` also read. No
    `dispatched` events: the reducer routes an item with no prior
    dispatch.

    A replay-level guard on the emptied-then-re-minted sequence, which
    no other test covers, and nothing more: it pins no property of
    `_mark_topic_dirty`'s upsert. Issuing that upsert through
    `session.connection().execute()` instead of `session.execute()`
    reds neither this test nor any other in the routing, `unroute`,
    `reenter` and `requeue` suites, because the ordering is supplied
    one layer below any fixture — `Session.merge(load=True)`
    autoflushes unconditionally, so the pending `Topic` DELETE reaches
    the connection at the *next* `_apply_route_event` before the
    upsert is issued. No event ordering can take that away, so there
    is no scenario to look for.

    When: #1 routes to "softwrap", #2 to "wrapping", #1 re-routes to
      "wrapping" (emptying "softwrap"), then #2 re-routes to "softwrap"
      (re-minting it) — replayed in one `rebuild_from_ledgers` call
    Then: both "softwrap" and "wrapping" exist and are dirty
    """
    routing_path = tmp_path / "reroute_routing.jsonl"
    routing: Ledger[RoutingEvent] = Ledger(routing_path)
    for event in (
        _route("I_1", "softwrap", "2026-01-01T00:00:00Z"),
        _route("I_2", "wrapping", "2026-01-02T00:00:00Z"),
        _route("I_1", "wrapping", "2026-01-03T00:00:00Z"),
        _route("I_2", "softwrap", "2026-01-04T00:00:00Z"),
    ):
        routing.append(event)

    store = Store.open(tmp_path / DB_NAME)
    store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)
        assert session.get(Topic, "wrapping") == Topic(name="wrapping", dirty=True)


def test_a_reminted_topic_clears_on_a_later_close(tmp_path: Path) -> None:
    """A close still clears a topic a later route had dirtied again.

    The reducer writes `Topic` through a Core upsert and reads it back
    through the ORM in `_apply_close_event`, in one `Session` for a
    whole replay, so the two views have to agree. `RETURNING` with
    `populate_existing` is what makes them: without it the upsert
    leaves an identity-mapped row reading its old `dirty` and
    persistent-clean with it, so the second close is a no-op change
    emitting no `UPDATE` and the topic stays dirty forever.

    `_pinned_topics` is load-bearing: the identity map is weak, so
    unpinned the stale instance is collected between events and the
    next `session.get` re-reads the true row — green whatever the
    upsert does. Mutation run: dropping the two clauses reds this test
    alone across the routing, `unroute`, `reenter` and `requeue`
    suites; dropping the pinning too makes it green under the defect.

    When: #1 routes to "softwrap", "softwrap" closes, #2 routes to
      "softwrap" (dirtying it again), then "softwrap" closes once more
      — replayed in one `rebuild_from_ledgers` call, holding every
      `Topic` the ORM loads
    Then: "softwrap" exists and is clean
    """
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "reclose_routing.jsonl")
    for entry in (
        _route("I_1", "softwrap", "2026-01-01T00:00:00Z"),
        {"type": "close", "data": {"token": "t1", "topic": "softwrap"}},
        _route("I_2", "softwrap", "2026-01-02T00:00:00Z"),
        {"type": "close", "data": {"token": "t2", "topic": "softwrap"}},
    ):
        routing.append(cast("RoutingEvent", entry))

    store = Store.open(tmp_path / DB_NAME)
    with _pinned_topics():
        store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=False)
