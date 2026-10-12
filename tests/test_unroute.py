"""Test `unroute`: withdrawing a verdict and releasing the topic it leaves."""

from pathlib import Path

import pytest
from sqlalchemy import select

from ghmem.aliases import GraphQLId
from ghmem.heat import HEAT_WEIGHTS
from ghmem.ledger import NO_SIGNAL, Ledger, UnrouteEvent
from ghmem.models import Dispatch, Route, Topic
from ghmem.status import build_status
from ghmem.topic import assemble
from tests.routing_helpers import (
    FETCH_FIXTURE,
    _dispatch,
    _live_store,
    _route,
    _session,
)

UNROUTE_1: UnrouteEvent = {"type": "unroute", "data": {"item_id": "I_1"}}
UNROUTE_2: UnrouteEvent = {"type": "unroute", "data": {"item_id": "I_2"}}
UNROUTE_3: UnrouteEvent = {"type": "unroute", "data": {"item_id": "I_3"}}


def test_unroute_deletes_the_route_row(tmp_path: Path) -> None:
    """Unrouting a routed item deletes its Route row outright.

    The deletion, not a superseding upsert, is the whole point: three
    freshness gates key off a Route's presence, and only its absence
    clears all three.

    When: item #1 is dispatched and routed, then unrouted
    Then: no Route row remains for it
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, _dispatch("I_1"))
    store.record_routing_event(
        routing, _route("I_1", "softwrap", "2026-01-01T00:00:00Z")
    )

    store.record_routing_event(routing, UNROUTE_1)

    with _session(tmp_path) as session:
        assert session.get(Route, GraphQLId("I_1")) is None


def test_unroute_deletes_the_dispatch_row(tmp_path: Path) -> None:
    """Unrouting also frees the item's outstanding Dispatch row.

    When: item #1 is dispatched and routed, then unrouted
    Then: its Dispatch row is gone too
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, _dispatch("I_1"))
    store.record_routing_event(
        routing, _route("I_1", "softwrap", "2026-01-01T00:00:00Z")
    )

    store.record_routing_event(routing, UNROUTE_1)

    with _session(tmp_path) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) is None


def test_unroute_dirties_a_topic_that_keeps_items(tmp_path: Path) -> None:
    """Unrouting one of a topic's items dirties the topic it leaves.

    The topic is cleaned first: a Topic row is born dirty, so asserting
    `dirty is True` against a fresh row would pass with the dirtying
    step removed.

    When: items #1 and #2 are routed to "softwrap", the topic is marked
      clean, then #1 is unrouted
    Then: "softwrap" survives and is dirty again
    """
    store, routing = _live_store(tmp_path)
    for item_id, stamp in (
        ("I_1", "2026-01-01T00:00:00Z"),
        ("I_2", "2026-01-02T00:00:00Z"),
    ):
        store.record_routing_event(routing, _dispatch(item_id))
        store.record_routing_event(routing, _route(item_id, "softwrap", stamp))
    with _session(tmp_path) as session:
        session.get(Topic, "softwrap").dirty = False  # type: ignore[union-attr]
        session.commit()

    store.record_routing_event(routing, UNROUTE_1)

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)


def test_unroute_of_the_last_item_drops_the_topic_row(tmp_path: Path) -> None:
    """Unrouting a topic's last item deletes the Topic row entirely.

    A Topic row exists exactly while some Route names it. Leaving an
    empty one behind would put a name in `status`'s dirty set that
    `topic.assemble` then refuses, stalling the synthesis loop.

    When: item #1 is the only item routed to "softwrap", then unrouted
    Then: no Topic row named "softwrap" remains
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, _dispatch("I_1"))
    store.record_routing_event(
        routing, _route("I_1", "softwrap", "2026-01-01T00:00:00Z")
    )

    store.record_routing_event(routing, UNROUTE_1)

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") is None


def test_unroute_of_a_no_signal_route_touches_no_topic(tmp_path: Path) -> None:
    """Unrouting a NO_SIGNAL verdict clears its rows and no topic.

    Uniform treatment: the verdict is withdrawn like any other, and the
    topic-release step is skipped because NO_SIGNAL names no topic.

    A *second* NO_SIGNAL route is left standing on purpose. With one
    only, releasing NO_SIGNAL would find nothing left and delete a row
    that never existed, so dropping the guard would look identical to
    keeping it; with one surviving, an unguarded release marks the
    reserved verdict dirty and mints a Topic row named "no-signal".

    When: items #2 and #3 are routed to NO_SIGNAL and #2 is unrouted
    Then: #2's Route and Dispatch are gone, #3 still carries its
      verdict, and no Topic named NO_SIGNAL was ever created
    """
    store, routing = _live_store(tmp_path)
    for item_id, stamp in (
        ("I_2", "2026-01-02T00:00:00Z"),
        ("I_3", "2026-01-03T00:00:00Z"),
    ):
        store.record_routing_event(routing, _dispatch(item_id))
        store.record_routing_event(routing, _route(item_id, NO_SIGNAL, stamp))

    store.record_routing_event(routing, UNROUTE_2)

    with _session(tmp_path) as session:
        assert session.get(Route, GraphQLId("I_2")) is None
        assert session.get(Dispatch, GraphQLId("I_2")) is None
        assert session.get(Route, GraphQLId("I_3")) is not None
        assert session.get(Topic, NO_SIGNAL) is None


def test_unroute_of_an_unrouted_item_is_a_no_op(tmp_path: Path) -> None:
    """Unrouting an item that carries no Route raises nothing.

    Mirrors `requeue`'s idempotent-delete contract, which is what makes
    a repeated `ghmem unroute` over the same batch safe.

    When: item #3, never dispatched or routed, is unrouted
    Then: recording it raises nothing, and a rebuild from the resulting
      ledger raises nothing either
    """
    store, routing = _live_store(tmp_path)

    store.record_routing_event(routing, UNROUTE_3)

    store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)


def test_unrouted_item_returns_to_the_untriaged_queue(tmp_path: Path) -> None:
    """An unrouted item is served again by the untriaged heat query.

    The end-to-end reason the event exists: `untriaged_heat_scores`
    excludes an item carrying a fresh Route, so only the Route's
    absence puts it back in front of a triage worker.

    When: item #1 is routed with a stamp matching its live updated_at,
      then unrouted
    Then: #1 is absent from untriaged_heat_scores while routed and
      present again after the unroute
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, _dispatch("I_1"))
    store.record_routing_event(
        routing, _route("I_1", "softwrap", "2026-01-01T00:00:00Z")
    )
    while_routed = [s.number for s in store.untriaged_heat_scores(HEAT_WEIGHTS)]

    store.record_routing_event(routing, UNROUTE_1)

    assert 1 not in while_routed
    assert 1 in [s.number for s in store.untriaged_heat_scores(HEAT_WEIGHTS)]


def test_live_unroute_matches_a_cold_rebuild(tmp_path: Path) -> None:
    """Recording unroutes live matches replaying the same ledger cold.

    Asserted over a fixture where both sides are non-empty: item #2
    stays routed throughout, so identical-and-empty cannot pass this.

    When: #1 and #2 are routed to "softwrap", #3 to "wrapping", then #1
      and #3 are unrouted live and the store is rebuilt from the ledger
    Then: Route, Dispatch and Topic rows are identical either way
    """
    store, routing = _live_store(tmp_path)
    for item_id, verdict, stamp in (
        ("I_1", "softwrap", "2026-01-01T00:00:00Z"),
        ("I_2", "softwrap", "2026-01-02T00:00:00Z"),
        ("I_3", "wrapping", "2026-01-03T00:00:00Z"),
    ):
        store.record_routing_event(routing, _dispatch(item_id))
        store.record_routing_event(routing, _route(item_id, verdict, stamp))
    store.record_routing_event(routing, UNROUTE_1)
    store.record_routing_event(routing, UNROUTE_3)

    with _session(tmp_path) as session:
        live = (
            list(session.execute(select(Route).order_by(Route.item_id)).scalars()),
            list(
                session.execute(select(Dispatch).order_by(Dispatch.item_id)).scalars()
            ),
            list(session.execute(select(Topic).order_by(Topic.name)).scalars()),
        )

    store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)

    with _session(tmp_path) as session:
        rebuilt = (
            list(session.execute(select(Route).order_by(Route.item_id)).scalars()),
            list(
                session.execute(select(Dispatch).order_by(Dispatch.item_id)).scalars()
            ),
            list(session.execute(select(Topic).order_by(Topic.name)).scalars()),
        )

    assert live[0]
    assert live[1]
    assert live[2]
    assert live == rebuilt


@pytest.mark.usefixtures("target_corpus")
def test_status_dirty_set_stays_assemblable_after_emptying_a_topic(
    tmp_path: Path,
) -> None:
    """Every name `status` reports dirty can still be assembled.

    FR-4 end to end: an emptied topic left in the dirty set would make
    the documented status-then-synthesize loop raise
    `TopicNotRoutedError` on a name the corpus itself handed out.

    When: #1 is routed alone to "softwrap", #2 and #3 to "wrapping",
      then #1 is unrouted
    Then: `build_status`'s dirty set is exactly ("wrapping",) and
      assembling each name in it raises nothing
    """
    store, routing = _live_store(tmp_path)
    for item_id, verdict, stamp in (
        ("I_1", "softwrap", "2026-01-01T00:00:00Z"),
        ("I_2", "wrapping", "2026-01-02T00:00:00Z"),
        ("I_3", "wrapping", "2026-01-03T00:00:00Z"),
    ):
        store.record_routing_event(routing, _dispatch(item_id))
        store.record_routing_event(routing, _route(item_id, verdict, stamp))

    store.record_routing_event(routing, UNROUTE_1)

    summary = build_status(store, Ledger(FETCH_FIXTURE))
    assert summary.dirty == ("wrapping",)
    assert summary.topics == ("wrapping",)
    for name in summary.dirty:
        assemble(store, routing, name)
