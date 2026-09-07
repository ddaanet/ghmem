"""Test the live routing write path: append to the ledger, then apply."""

from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import select

from ghmem.aliases import GraphQLId
from ghmem.exceptions import UnknownRoutingEventError
from ghmem.ledger import NO_SIGNAL, FetchEvent, Ledger, RouteEvent, RoutingEvent
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
        "item_updated_at": "2026-01-01T00:00:00Z",
    },
}
ROUTE_WRAPPING: RouteEvent = {
    "type": "route",
    "data": {
        "session": "s2",
        "item_id": "I_1",
        "verdict": "wrapping",
        "note": "actually about wrapping",
        "item_updated_at": "2026-01-01T00:00:00Z",
    },
}


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
        {"type": "bogus", "data": {"session": "s1", "item_id": "I_1"}},
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
            item_updated_at=datetime.fromisoformat("2026-01-01T00:00:00Z"),
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
    assert {t.name for t in topics} == {"wrapping"}
    assert live == rebuilt


def test_reroute_of_the_last_item_drops_the_topic_row(tmp_path: Path) -> None:
    """Re-routing a topic's last item away drops its Topic row.

    A Topic row exists exactly while some Route names it, whichever
    event empties it: `unroute`'s withdrawal and a re-route away are
    one rule. This is the live counterpart of `test_routing_replay.py`'s
    `test_reroute_replaces_the_verdict_and_drops_the_emptied_topic` —
    the same rule reached one event at a time through
    `record_routing_event` rather than through a whole-ledger replay.

    When: item #1 is routed to "softwrap" alone, then re-routed to
      "wrapping"
    Then: "softwrap" no longer exists, and "wrapping" exists and is
      dirty
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, ROUTE_SOFTWRAP)

    store.record_routing_event(routing, ROUTE_WRAPPING)

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") is None
        assert session.get(Topic, "wrapping") == Topic(name="wrapping", dirty=True)


def test_status_dirty_set_stays_assemblable_after_a_reroute(
    tmp_path: Path,
) -> None:
    """Every name `status` reports dirty can still be assembled.

    FR-4 end to end: an emptied topic left in the dirty set would make
    the documented status-then-synthesize loop raise
    `TopicNotRoutedError` on a name the corpus itself handed out.

    When: #1 is routed alone to "softwrap", #2 and #3 to "wrapping",
      then #1 is re-routed to "wrapping"
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

    store.record_routing_event(
        routing, _route("I_1", "wrapping", "2026-01-01T00:00:00Z")
    )

    summary = build_status(store, Ledger(FETCH_FIXTURE))
    assert summary.dirty == ("wrapping",)
    assert summary.topics == ("wrapping",)
    for name in summary.dirty:
        assemble(store, routing, name)


def test_reroute_keeps_a_topic_that_keeps_items(tmp_path: Path) -> None:
    """A topic that keeps a sibling survives, dirty.

    A regression guard: `_release_topic`'s `remaining` branch is already
    covered from the `unroute` side by
    `test_unroute_dirties_a_topic_that_keeps_items`; this is the sole
    coverage that the `route` branch reaches it too. The topic is
    cleaned first — a Topic row is born dirty, so asserting `dirty is
    True` against a fresh row would pass with the dirtying step
    removed.

    Mutation run: making the route-side release skip `_mark_topic_dirty`
    on `_release_topic`'s `remaining` branch reds this test alone —
    `test_same_topic_reroute_keeps_the_topic_and_the_route`,
    `test_a_topic_reminted_after_being_emptied_in_one_replay_survives`,
    and `test_unroute_dirties_a_topic_that_keeps_items` all stay green
    under it.

    When: items #1 and #2 are routed to "softwrap", the topic is marked
      clean, then #1 is re-routed to "wrapping"
    Then: "softwrap" survives and is dirty again
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(
        routing, _route("I_1", "softwrap", "2026-01-01T00:00:00Z")
    )
    store.record_routing_event(
        routing, _route("I_2", "softwrap", "2026-01-02T00:00:00Z")
    )
    with _session(tmp_path) as session:
        session.get(Topic, "softwrap").dirty = False  # type: ignore[union-attr]
        session.commit()

    store.record_routing_event(
        routing, _route("I_1", "wrapping", "2026-01-01T00:00:00Z")
    )

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)


def test_same_topic_reroute_keeps_the_topic_and_the_route(tmp_path: Path) -> None:
    """A same-topic re-route keeps both the topic and the Route row.

    Also a guard, and the only coverage this path has: `record_verdict`
    refuses a live re-route, so a same-topic re-route is reachable only
    through re-entry. The second event carries a different session and
    note than the first, so the merge is observable rather than a
    replayed no-op.

    Mutation run, and not discriminated: rewriting the release as a
    delete-then-recreate — tried both as `_release_topic`'s own
    Topic-row handling and as the Route replacement in
    `_apply_route_event`, mirroring `_apply_unroute_event`'s delete of
    its own Route row — reds neither this test nor any other test in
    the routing suites. SQLAlchemy orders the delete before the insert
    for the same primary key within one flush either way, so no
    collision or wipe was observed under either reading. Recorded
    honestly rather than engineered into a red; the mutation the
    runbook named for this test does not discriminate it as written.

    When: #1 is routed to "softwrap", the topic is marked clean, then a
      second "route" event sends #1 to "softwrap" again from a
      different session with a different note
    Then: "softwrap" is still dirty, and the Route row matches the
      second event in full
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(
        routing, _route("I_1", "softwrap", "2026-01-01T00:00:00Z")
    )
    with _session(tmp_path) as session:
        session.get(Topic, "softwrap").dirty = False  # type: ignore[union-attr]
        session.commit()

    second: RouteEvent = {
        "type": "route",
        "data": {
            "session": "s2",
            "item_id": "I_1",
            "verdict": "softwrap",
            "note": "still about softwrap",
            "item_updated_at": "2026-01-02T00:00:00Z",
        },
    }
    store.record_routing_event(routing, second)

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)
        assert session.get(Route, GraphQLId("I_1")) == Route(
            item_id=GraphQLId("I_1"),
            verdict="softwrap",
            note="still about softwrap",
            session="s2",
            item_updated_at=datetime.fromisoformat("2026-01-02T00:00:00Z"),
        )


def test_route_to_no_signal_drops_the_emptied_topic(tmp_path: Path) -> None:
    """Routing a topic's last item to NO_SIGNAL drops the topic too.

    The change is not confined to movement between topics: NO_SIGNAL
    mints no `Topic` of its own, but a route to it still releases the
    topic the item leaves.

    When: item #1 is routed to "softwrap" alone, then re-routed to
      NO_SIGNAL
    Then: "softwrap" no longer exists, and NO_SIGNAL still names no
      topic
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, ROUTE_SOFTWRAP)

    store.record_routing_event(
        routing, _route("I_1", NO_SIGNAL, "2026-01-01T00:00:00Z")
    )

    with _session(tmp_path) as session:
        assert session.get(Topic, "softwrap") is None
        assert session.get(Topic, NO_SIGNAL) is None
