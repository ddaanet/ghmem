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

from typing import cast

from sqlalchemy import CursorResult, Engine, exists, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId
from ghmem.ingest import apply_routing_event
from ghmem.ledger import DispatchedEvent, Ledger, RoutingEvent
from ghmem.models import Dispatch, IssueOrPR, Route, Topic


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
