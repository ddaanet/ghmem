"""The routing reducer: one routing event applied to the SQLite mirror.

`apply_routing_event` is the single write path for `Route`, `Dispatch`,
`Topic` and `Snapshot`. Both `ingest.rebuild_from_ledgers`'s cold
replay and every live command in `dispatch_store.py` go through it, so
a live write and a rebuild cannot drift — the architecture's central
claim.

Split from `ingest.py` along the seam its module docstring already
draws: that module owns the fetch ledger's raw nodes and the drop-and-
replay driver, this one owns what a routing event means. The two share
only the direction of travel, ledger into SQLite.
"""

from datetime import datetime
from typing import Final

import msgspec
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId
from ghmem.exceptions import InvalidItemUpdatedAtError, UnknownRoutingEventError
from ghmem.ledger import (
    NO_SIGNAL,
    CloseData,
    CloseEvent,
    DispatchedData,
    DispatchedEvent,
    ReenterData,
    ReenterEvent,
    RequeueData,
    RequeueEvent,
    RouteData,
    RouteEvent,
    RoutingEvent,
    SnapshotData,
    SnapshotEvent,
    UnrouteData,
    UnrouteEvent,
)
from ghmem.models import Dispatch, Route, Snapshot, Topic


def apply_routing_event(session: Session, event: RoutingEvent) -> None:
    """Apply one routing event to the SQLite mirror.

    Called both by `rebuild_from_ledgers`'s replay and, via
    `Store.record_routing_event`, by every live command except
    `reserve_dispatch`, so a live write through here and a cold rebuild
    can never diverge. `reserve_dispatch` writes its `Dispatch` row
    directly, outside this reducer; see its own docstring for why.

    `"dispatched"` upserts a `Dispatch` row by item id.

    `"route"` replaces the item's `Route` row (latest-wins), dirties
    the topic it's joining if the new verdict isn't `NO_SIGNAL`, and
    releases the topic it's leaving if it had a real (non-`NO_SIGNAL`)
    verdict before — the same release `"unroute"` performs below, so a
    re-route that empties its old topic drops that row rather than
    leaving it dirty.

    `"requeue"` deletes the item's `Dispatch` row, freeing it to be
    served again. Deleting an already-absent row is a no-op, not an
    error, so replay of a requeue stays idempotent — see `Dispatch`'s
    docstring for why the delete is load-bearing.

    `"snapshot"` upserts a `Snapshot` row by token — a plain record of
    what `topic.assemble` minted, touching no `Topic.dirty` flag.

    `"close"` clears the named topic's `Topic.dirty` flag
    unconditionally on the event's presence — no currency recheck
    here, since `close_topic` already performed that compare-and-set
    before ever writing this event. A missing `Topic` row is a no-op.

    `"unroute"` withdraws a verdict outright, deleting the item's
    `Route` and `Dispatch` together so it rejoins the triage queue, and
    releasing the topic it leaves.

    Any other tag raises: the union grows a member per dispatch part,
    and a tag with no branch here means the ledger is ahead of the code,
    not that a verdict can be guessed.

    Every event is put through `validate_routing_event` first, so the
    branches below narrow a shape already known to hold rather than
    one asserted by `Ledger.__iter__`'s `cast`.
    """
    validate_routing_event(event)
    # Captured before the checks below narrow `event` away: statically
    # the union is exhausted by them, but a ledger line is untyped JSON
    # at runtime and can carry anything.
    event_type: str = event["type"]
    if event["type"] == "dispatched":
        _apply_dispatched_event(session, event["data"])
    elif event["type"] == "route":
        _apply_route_event(session, event["data"])
    elif event["type"] == "requeue":
        _apply_requeue_event(session, event["data"])
    elif event["type"] == "snapshot":
        _apply_snapshot_event(session, event["data"])
    elif event["type"] == "close":
        _apply_close_event(session, event["data"])
    elif event["type"] == "reenter":
        _apply_reenter_event(session, event["data"])
    elif event["type"] == "unroute":
        _apply_unroute_event(session, event["data"])
    else:
        raise UnknownRoutingEventError(event_type)


def _apply_dispatched_event(session: Session, data: DispatchedData) -> None:
    """Record that an item is outstanding to a session, upserting by item id."""
    session.merge(
        Dispatch(
            item_id=GraphQLId(data["item_id"]),
            session=data["session"],
            packet_chars=data["packet_chars"],
        )
    )


def _apply_route_event(session: Session, data: RouteData) -> None:
    """Replace an item's verdict, releasing the topic it leaves."""
    item_id = GraphQLId(data["item_id"])
    previous = session.get(Route, item_id)
    left = previous.verdict if previous is not None else None
    session.merge(
        Route(
            item_id=item_id,
            verdict=data["verdict"],
            note=data["note"],
            session=data["session"],
            item_updated_at=_parse_item_updated_at(item_id, data["item_updated_at"]),
        )
    )
    if data["verdict"] != NO_SIGNAL:
        _mark_topic_dirty(session, data["verdict"])
    if left is not None and left != NO_SIGNAL:
        _release_topic(session, left)


def _apply_requeue_event(session: Session, data: RequeueData) -> None:
    """Free a stranded dispatch, leaving an already-absent row alone."""
    dispatch = session.get(Dispatch, GraphQLId(data["item_id"]))
    if dispatch is not None:
        session.delete(dispatch)


def _parse_item_updated_at(item_id: str, raw: str) -> datetime:
    """Parse a `route` event's `item_updated_at`, rejecting a poison pill.

    A hand-appended `route` event (see `RouteData.item_updated_at`'s
    docstring) can carry a naive or unparseable timestamp. Left
    unguarded, that value reaches `Route.item_updated_at`, where it
    breaks two downstream comparisons that only agree when every stamp
    is timezone-aware UTC: `stale_routes`' textual SQL comparison
    (silently — a naive string just sorts differently) and
    `record_verdict`'s Python `datetime` comparison (with an uncaught
    `TypeError`). Both failure modes are collapsed into one
    `InvalidItemUpdatedAtError` here, at parse time, naming the item
    and the raw string, instead of surfacing later from whichever
    comparison reaches it first.

    Only tz-awareness is checked, not a specific UTC offset — a
    tz-aware but non-UTC stamp is accepted; enforcing UTC specifically
    is a separate, later concern.
    """
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise InvalidItemUpdatedAtError(item_id, raw) from error
    if parsed.tzinfo is None:
        raise InvalidItemUpdatedAtError(item_id, raw)
    return parsed


def _apply_snapshot_event(session: Session, data: SnapshotData) -> None:
    """Record a minted snapshot token for a topic."""
    session.merge(
        Snapshot(token=data["token"], topic=data["topic"], digest=data["digest"])
    )


def _apply_close_event(session: Session, data: CloseData) -> None:
    """Clear the dirty flag for a closed topic."""
    topic = session.get(Topic, data["topic"])
    if topic is not None:
        topic.dirty = False


def _apply_reenter_event(session: Session, data: ReenterData) -> None:
    """Free a stale route's dispatch and dirty its topic if it was routed."""
    item_id = GraphQLId(data["item_id"])
    dispatch = session.get(Dispatch, item_id)
    if dispatch is not None:
        session.delete(dispatch)
    route = session.get(Route, item_id)
    if route is not None and route.verdict != NO_SIGNAL:
        _mark_topic_dirty(session, route.verdict)


def _apply_unroute_event(session: Session, data: UnrouteData) -> None:
    """Withdraw a verdict: delete its Route and Dispatch, release its topic.

    The `Route` delete is what separates this from `"reenter"`, which
    frees only the `Dispatch`. Three gates each exclude an item
    carrying a fresh `Route` — `untriaged_heat_scores` never serves it,
    `stale_routes` never reenters it, `record_verdict` refuses a second
    verdict for it — and only the row's absence clears all three at
    once, without relaxing any of them.

    Both deletes are idempotent, matching `"requeue"`: an item with no
    `Route` and no `Dispatch` is left alone rather than raising, so a
    repeated `ghmem unroute` over the same batch replays cleanly.

    Every verdict is treated alike, `NO_SIGNAL` included. Only the
    topic-release step distinguishes them, and it is skipped for
    `NO_SIGNAL` for the same reason the `"route"` and `"reenter"`
    branches skip it: `NO_SIGNAL` names no topic.
    """
    item_id = GraphQLId(data["item_id"])
    dispatch = session.get(Dispatch, item_id)
    if dispatch is not None:
        session.delete(dispatch)
    route = session.get(Route, item_id)
    if route is None:
        return
    left = route.verdict
    session.delete(route)
    if left != NO_SIGNAL:
        _release_topic(session, left)


def _release_topic(session: Session, name: str) -> None:
    """Dirty topic `name`, or drop its row once no route names it any more.

    A `Topic` row exists exactly while some `Route` carries its name.
    An emptied row left behind would sit in `status`'s dirty set as a
    name `topic.assemble` then refuses with `TopicNotRoutedError`,
    stalling the documented synthesis loop on a name the corpus itself
    handed out. Withdrawal and re-route both empty a topic this way,
    and both release it here.

    Nothing here touches `docs/design/<name>.md`. This reducer is
    replayed wholesale by `rebuild_from_ledgers`, so a filesystem
    delete inside it would make a rebuild destructive, and `ghmem
    index` keeps listing the orphaned doc from disk.

    `Snapshot` rows outlive the dropped `Topic` too, and are left
    alone for a different reason: `close_topic`
    (`dispatch_store.py`) is a compare-and-set against the topic's
    *live* digest, recomputed at close time. An emptied topic hashes
    an empty routed set, which cannot match the digest stored when
    the snapshot was taken, so a token outstanding over a dropped
    topic is declined — `cleared=False`, nothing written, nothing
    appended — rather than honoured against a name no route carries.
    """
    # The caller's write on the route being withdrawn or replaced — a
    # `delete` under `unroute`, an `update` under `route` — reaches the
    # database before this query either way, emitted by SQLAlchemy's
    # autoflush: at the join-side `_mark_topic_dirty`'s
    # `session.execute` where a re-route has one, and at this query
    # otherwise. So the count excludes that route regardless of which
    # write it was. SQLite's writer lock is held from that write
    # onwards, so the COUNT and the DELETE below land on the same side
    # of it: a worker routing another item into this topic cannot
    # commit between them. Either the count sees the sibling and
    # dirties, or the topic is dropped and the sibling's route re-mints
    # it — never a lost update.
    remaining = session.scalar(
        select(func.count()).select_from(Route).where(Route.verdict == name)
    )
    if remaining:
        _mark_topic_dirty(session, name)
        return
    topic = session.get(Topic, name)
    if topic is not None:
        session.delete(topic)


def _mark_topic_dirty(session: Session, name: str) -> None:
    """Get-or-create the `Topic` named `name`, then mark it dirty.

    A conditional upsert rather than a get-then-add, for the reason
    `reserve_dispatch`'s docstring (`dispatch_store.py`) gives: the
    `sqlite3` driver opens a transaction before a write but not before
    a read, so two workers routing different items into the same
    not-yet-existing topic could both read `None` and race the
    `INSERT`.

    Issued through the reducer's own `session.execute()` rather than
    the connection directly, so its autoflush emits a pending `Topic`
    `DELETE` ahead of the insert: `rebuild_from_ledgers` replays the
    whole routing ledger in one `Session`, so a topic emptied at one
    event and re-minted at a later one is a within-session delete-
    then-recreate, and an insert that reached the connection ahead of
    that flush would be undone by the `DELETE` landing behind it.

    A guard rather than the thing holding this path up, on the
    evidence: every route into a topic already flushes before
    reaching here, at `session.merge`'s unconditional autoflush in
    `_apply_route_event` or at `_release_topic`'s `COUNT`. Issuing
    the same statement on the connection was measured against every
    branch that reaches this helper and lost no row.
    """
    stmt = (
        sqlite_insert(Topic)
        .values(name=name, dirty=True)
        .on_conflict_do_update(index_elements=["name"], set_={"dirty": True})
    )
    session.execute(stmt)


def validate_routing_event(event: RoutingEvent) -> None:
    """Validate `event` against the shape its `RoutingEvent` member declares.

    The routing counterpart of `validate_raw_node`, and the one that
    matters more: `fetch.jsonl` is regenerable from GitHub, while
    `routing.jsonl` is the system's only original record.

    A field missing from a routing event otherwise survives both
    gates that look like they would catch it. Static typing cannot
    see through `Ledger.__iter__`'s `cast`, which hands out a
    `RoutingEvent` on the strength of the annotation alone, and the
    reducer below reads only the fields its own branches touch — so a
    `dispatched` event with no `fit` replays cleanly and is found
    missing by whichever consumer first wants it, long after the
    ledger was written.

    `apply_routing_event` calls this first, which puts it on both
    paths at once: the live write, before `Store.record_routing_event`
    appends anything, and replay from `rebuild_from_ledgers`.

    Raises `UnknownRoutingEventError` for a tag no member declares,
    and `msgspec.ValidationError` for a member whose shape does not
    hold. An unknown *key* is not a violation — msgspec ignores extra
    keys on a TypedDict — because a ledger written by a later part
    may carry a field this code has no reducer branch for yet, which
    is the direction that must stay readable.
    """
    event_type: str = event["type"]
    if event_type not in _ROUTING_BY_TYPE:
        raise UnknownRoutingEventError(event_type)
    msgspec.convert(event, _ROUTING_BY_TYPE[event_type])


_ROUTING_BY_TYPE: Final[dict[str, type]] = {
    "dispatched": DispatchedEvent,
    "route": RouteEvent,
    "requeue": RequeueEvent,
    "snapshot": SnapshotEvent,
    "close": CloseEvent,
    "reenter": ReenterEvent,
    "unroute": UnrouteEvent,
}
"""Each routing tag mapped to the TypedDict that declares its shape.

Mirrors `raw_nodes.py`'s `_RAW_BY_TYPE`, and checks one way only, the
same way: a tag here with no branch in `apply_routing_event` still
reaches that function's closing raise.
"""
