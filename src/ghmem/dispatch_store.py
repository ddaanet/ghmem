"""Triage-state reads and writes: the dispatch commands' own tables.

Split out of `store.py` along the seam that already separates the two
halves of the mirror. `Dispatch`, `Route`, and `Topic` are authored by
the dispatch commands and backed by the routing ledger — unlike the
corpus tables, which are a regenerable cache of the fetch ledger, this
one cannot be regenerated. This module carries the whole concurrency
story that follows from that: the writer lock, the busy timeout, the
conditional insert, and the append-before-commit ordering.

`Store`'s methods over these tables delegate here, mirroring how
`Store.heat_scores` delegates to `heat_query.py`.
"""

import hashlib
from dataclasses import dataclass
from typing import cast

from sqlalchemy import CursorResult, Engine, exists, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId
from ghmem.exceptions import UnknownSnapshotError
from ghmem.ingest import apply_routing_event
from ghmem.ledger import NO_SIGNAL, CloseEvent, DispatchedEvent, Ledger, RoutingEvent
from ghmem.models import Dispatch, IssueOrPR, Route, Snapshot, Topic


def record_routing_event(
    engine: Engine, routing: Ledger[RoutingEvent], event: RoutingEvent
) -> None:
    """Record one routing event in both the ledger and the mirror.

    Every mutating dispatch command but `reserve_dispatch` goes
    through here, applying the event through the same
    `apply_routing_event` reducer that `rebuild_from_ledgers`
    replays — why those live writes cannot drift from a cold
    rebuild. `reserve_dispatch` is the exception; see its docstring.

    The three steps interleave deliberately. The reducer runs first,
    but inside an uncommitted transaction, so an event it rejects
    never reaches `routing.jsonl` — the one file here that cannot be
    regenerated, and the one replay is strict about. This is the
    parse-before-append rule the fetch driver follows via
    `validate_raw_node`, which the routing ledger needs more, not
    less: a poison line there makes every future rebuild raise,
    whereas a bad fetch line can simply be refetched.

    The ledger is then appended *before* the mirror commits, because
    it is the durable truth and the mirror is a regenerable cache. A
    crash in between loses nothing: the next rebuild replays the
    event that already landed. The reverse order could commit a
    verdict that no ledger records, which no rebuild would restore.

    Durability beyond the OS is the caller's checkpoint to take, via
    `Ledger.sync()` — an fsync per routed item would tax a triage
    session for a guarantee that only matters at run boundaries.
    """
    with Session(engine) as session:
        apply_routing_event(session, event)
        routing.append(event)
        session.commit()


def session_usage(engine: Engine, session_token: str) -> tuple[int, int]:
    """Return (items, chars) dispatched to `session_token` so far.

    Exact, not approximate, because of `Dispatch`'s item-id-only
    key: concurrent sessions are handed distinct items, and
    `requeue` only ever deletes a row with no session live, so
    every item a running session was dispatched still carries that
    session here for the whole session's lifetime.
    """
    with Session(engine) as session:
        items, chars = session.execute(
            select(
                func.count(Dispatch.item_id),
                func.coalesce(func.sum(Dispatch.packet_chars), 0),
            ).where(Dispatch.session == session_token)
        ).one()
    return (items, chars)


BUSY_TIMEOUT_SECONDS = 30
"""How long a writer waits on SQLite's lock before raising, not 5s.

`reserve_dispatch`'s conditional insert leans on SQLite's one-writer
lock to serialize concurrent reservations, so a busy-waiting writer is
the expected case, not a bug — set explicitly rather than trusting
pysqlite's 5-second default to still be right if it ever changes.
"""


def reserve_dispatch(
    engine: Engine, routing: Ledger[RoutingEvent], event: DispatchedEvent
) -> bool:
    """Atomically reserve an item for a session; False if already taken.

    The live dispatch path, and the one place two triage sessions
    can race for the same item: `Dispatch` is keyed on `item_id`
    alone, so whichever session's row lands first wins, and the
    loser must learn that rather than silently overwrite the
    winner. A read-then-write can't enforce that — the `sqlite3`
    driver opens a transaction before a write but not before a
    read, so two processes can both read "free" and both write.
    This collapses the check and the write into one conditional
    insert (`INSERT ... ON CONFLICT (item_id) DO NOTHING`), which
    SQLite evaluates inside the single writer's transaction — there
    is no window between "checked" and "written" for a second
    writer to land in.

    This is why the insert here is conditional while
    `apply_routing_event`'s "dispatched" branch is an unconditional
    `session.merge`: replay re-applies events that already
    happened and must never decline one on a technicality, but the
    live path is exactly the technicality — declining a second
    reservation for the same item is the whole point.

    The ledger append sits between the insert and the commit,
    inside the same transaction, so SQLite's writer lock serializes
    it as a side effect of the insert: a second reservation cannot
    observe the ledger between another session's insert and its
    append. Moving the append outside the transaction would give
    that up.
    """
    data = event["data"]
    stmt = (
        sqlite_insert(Dispatch)
        .values(
            item_id=GraphQLId(data["item_id"]),
            session=data["session"],
            packet_chars=data["packet_chars"],
        )
        .on_conflict_do_nothing(index_elements=["item_id"])
    )
    with Session(engine) as session:
        # No `.returning()`, so mypy sees the untyped `Result` overload.
        reserved = cast("CursorResult[None]", session.execute(stmt)).rowcount == 1
        if reserved:
            routing.append(event)
            session.commit()
    return reserved


def routes(engine: Engine) -> list[Route]:
    """Return the current triage verdict of every item in the corpus.

    Constrained to fetched items, as `dispatched_unrouted` is: the
    routing ledger outlives the regenerable fetch ledger, so a
    verdict naming an absent item is a steady state, not corruption.

    Counting one mixes two populations; `--requeue` cannot serve one.
    """
    with Session(engine) as session:
        return list(
            session.execute(
                select(Route)
                .where(Route.item_id.in_(select(IssueOrPR.id)))
                .order_by(Route.item_id)
            ).scalars()
        )


def dispatched_unrouted(engine: Engine) -> list[Dispatch]:
    """Return corpus items dispatched but carrying no current Route."""
    with Session(engine) as session:
        return list(
            session.execute(
                select(Dispatch)
                .where(Dispatch.item_id.in_(select(IssueOrPR.id)))
                .where(~exists().where(Route.item_id == Dispatch.item_id))
                .order_by(Dispatch.item_id)
            ).scalars()
        )


def dispatch_for(engine: Engine, item_id: GraphQLId) -> Dispatch | None:
    """Return `item_id`'s current `Dispatch` row, or None if not outstanding.

    A single-row lookup by primary key, for callers — `record_verdict`
    among them — that need one item's ownership rather than every
    outstanding dispatch in the corpus.
    """
    with Session(engine) as session:
        return session.get(Dispatch, item_id)


def route_for(engine: Engine, item_id: GraphQLId) -> Route | None:
    """Return `item_id`'s current `Route` row, or None if unrouted."""
    with Session(engine) as session:
        return session.get(Route, item_id)


def topics(engine: Engine) -> list[Topic]:
    """Return every known topic, ordered by name."""
    with Session(engine) as session:
        return list(session.execute(select(Topic).order_by(Topic.name)).scalars())


def _topic_digest(session: Session, name: str) -> str:
    """Hash topic `name`'s current routed-item set into one content digest.

    The single definition: `topic.assemble` never computes this a
    second way, it only asks `topic_digest` for the result.

    The join with `IssueOrPR` does two jobs at once — it drops routes
    to items the (regenerable) fetch ledger no longer carries,
    matching `routes()`'s corpus constraint, and it supplies each
    item's `updated_at`, which `Route` itself does not carry.
    `routed_items` filters by the same `verdict == name` join
    predicate — including the `verdict != NO_SIGNAL` exclusion below —
    so its item set can never disagree with what this digest
    summarizes. Both filters must change in lockstep.

    `Route.verdict == name` already excludes `NO_SIGNAL` whenever
    `name` is a real topic, since `NO_SIGNAL` can never equal a topic
    name — but the explicit `!= NO_SIGNAL` here also blocks the
    degenerate case where `name` itself is `"no-signal"`, which would
    otherwise report a bogus "topic" of every reviewed-nothing-here
    item.

    Ordering by `item_id` gives the hash a canonical input regardless
    of SQLite's own row order, so the same routed set always hashes to
    the same digest.
    """
    rows = session.execute(
        select(Route.item_id, IssueOrPR.updated_at)
        .join(IssueOrPR, IssueOrPR.id == Route.item_id)
        .where(Route.verdict == name, Route.verdict != NO_SIGNAL)
        .order_by(Route.item_id)
    ).all()
    payload = "\n".join(
        f"{item_id}\t{updated_at.isoformat()}" for item_id, updated_at in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def topic_digest(engine: Engine, name: str) -> str:
    """Open a session and hash topic `name`'s current routed-item set."""
    with Session(engine) as session:
        return _topic_digest(session, name)


@dataclass(frozen=True, slots=True)
class CloseOutcome:
    """The result of a `close_topic` compare-and-set attempt.

    `cleared` tells the two outcomes apart: `True` means the topic's
    dirty flag was cleared and a `CloseEvent` was appended; `False`
    means the topic changed since its snapshot and nothing was
    written at all.
    """

    topic: str
    cleared: bool


def close_topic(
    engine: Engine, routing: Ledger[RoutingEvent], token: str
) -> CloseOutcome:
    """Clear `token`'s topic dirty flag if its snapshot is still current.

    Raises `UnknownSnapshotError` if `token` names no `Snapshot` row —
    nothing that `topic.assemble` ever minted.

    The compare-and-set: the snapshot's stored digest is checked
    against the topic's *live* digest, recomputed here and now, never
    against itself. A mismatch means the topic changed after the
    snapshot was taken — writes nothing, appends nothing, commits
    nothing, and returns `cleared=False` so the caller can tell the
    close was declined rather than that there was nothing to close.

    A match appends exactly one `CloseEvent`, applies it through the
    shared reducer so the live write and a cold rebuild can never
    disagree, and commits. The ledger append happens *before* the
    mirror's commit — durable-truth-first, the same ordering
    `record_routing_event` uses — so a crash between the two loses
    nothing: the next rebuild replays the event that already landed.

    The read, the decision, the ledger append, and the mirror commit
    all share one `Session`, the compare-and-set sibling of
    `reserve_dispatch`: splitting the read from the write would open a
    window for the topic to change in between. No `IMMEDIATE`
    transaction is needed, unlike `reserve_dispatch` — `close` never
    races `route`, and distinct synthesis agents close distinct
    topics.
    """
    with Session(engine) as session:
        snapshot = session.get(Snapshot, token)
        if snapshot is None:
            raise UnknownSnapshotError(token)
        current = _topic_digest(session, snapshot.topic)
        if current != snapshot.digest:
            return CloseOutcome(snapshot.topic, cleared=False)
        event: CloseEvent = {
            "type": "close",
            "data": {"token": token, "topic": snapshot.topic},
        }
        apply_routing_event(session, event)
        routing.append(event)
        session.commit()
        return CloseOutcome(snapshot.topic, cleared=True)


def routed_items(engine: Engine, name: str) -> list[tuple[IssueOrPR, Route]]:
    """Return (item, route) pairs currently routed to topic `name`.

    Shares `_topic_digest`'s join and predicate exactly — including the
    `verdict != NO_SIGNAL` exclusion — so the item set this returns and
    the digest that summarizes it can never disagree about membership.
    The two filters must change in lockstep.
    """
    with Session(engine) as session:
        rows = session.execute(
            select(IssueOrPR, Route)
            .join(Route, Route.item_id == IssueOrPR.id)
            .where(Route.verdict == name, Route.verdict != NO_SIGNAL)
            .order_by(Route.item_id)
        ).all()
    return [(item, route) for item, route in rows]
