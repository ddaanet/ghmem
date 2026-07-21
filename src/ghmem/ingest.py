"""Raw-node ingest: ledger replay into SQLite.

This is the write half of the store: `rebuild_from_ledgers` drops and
replays the whole mirror from the fetch and routing ledgers, and
`validate_raw_node` is the parse-before-append entry the fetch driver
calls. The raw-node contract those replay — one `_Raw*` struct per node
kind — lives in `raw_nodes.py`; the schema and row identity live in
`models.py`'s declarative entities; the read half (the `Store` query
API) lives in `store.py`.

Two event types bypass the raw structs entirely: `"cursor"` is consumed
by `sync.py` and never stored here, and `"fetch_failed"` carries its
own `{"id", "reason"}` shape (`FetchFailedData` in `ledger.py`)
straight into the `fetch_failed` table.

Replay being strict (`forbid_unknown_fields=True` on every raw node),
the fetch driver must parse-then-append — validate a node against its
`_RawNode` struct *before* writing it. This is not a data-loss guard;
the ledger is JSONL, so a bad line can be repaired or dropped by hand.
It is about where the error surfaces: validating at fetch time turns an
ingestion error into a sync error, attributable to the call that
produced the bad node, instead of one `rebuild_from_ledgers` raises
later with only a JSON line to work back from.
"""

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId, JsonObject
from ghmem.exceptions import UnknownRoutingEventError
from ghmem.ledger import NO_SIGNAL, FetchEvent, Ledger, RoutingEvent
from ghmem.models import Base, Dispatch, FetchFailed, Route, Topic
from ghmem.raw_nodes import _RAW_BY_TYPE


def rebuild_from_ledgers(
    engine: Engine,
    fetch: Ledger[FetchEvent],
    routing: Ledger[RoutingEvent],
) -> None:
    """Drop and repopulate every table by replaying both ledgers.

    Replay is idempotent: the whole schema is dropped and recreated
    first, and every row is upserted by id, so rebuilding twice from
    the same ledgers leaves identical query results. Routing replays
    after fetch, since a route references an item that must already
    exist.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for event in fetch:
            _replay_fetch_event(session, event)
        for event in routing:
            apply_routing_event(session, event)
        session.commit()


def validate_raw_node(event_type: str, data: JsonObject) -> None:
    """Validate `data` against `event_type`'s raw-node contract.

    The public half of parse-before-append: the sync driver calls this
    on every node's `data` dict before appending it to the fetch
    ledger, so a contract violation surfaces as a sync error naming the
    call that produced it, instead of something `rebuild_from_ledgers`
    raises later with only a ledger line to reason back from. Raises
    `msgspec.ValidationError` on a violation (or `KeyError` if
    `event_type` isn't a raw-node kind, e.g. `"cursor"` or
    `"fetch_failed"`, neither of which has a `_Raw*` struct). Reuses
    `_RawNode.parse`, discarding the entity it builds — only the
    validation it performs as a side effect is wanted here.
    """
    _RAW_BY_TYPE[event_type].parse(data)


def _replay_fetch_event(session: Session, event: FetchEvent) -> None:
    """Dispatch one `FetchEvent` to its table-specific upsert.

    `session.merge` is the upsert: it matches an existing row by primary
    key and updates it in place, or inserts a new one — the same "insert
    or replace by id" semantics the hand-rolled SQL used. `"cursor"` is
    consumed by `sync.py`, not stored; `"fetch_failed"` has
    its own flat shape; every other type is a raw node dispatched by
    `_RAW_BY_TYPE`.
    """
    if event["type"] == "cursor":
        return
    if event["type"] == "fetch_failed":
        data = event["data"]
        session.merge(FetchFailed(id=data["id"], reason=data["reason"]))
        return
    session.merge(_RAW_BY_TYPE[event["type"]].parse(event["data"]))


def apply_routing_event(session: Session, event: RoutingEvent) -> None:
    """Apply one routing event to the SQLite mirror.

    Called both by `rebuild_from_ledgers`'s replay and, via
    `Store.record_routing_event`, by each live command right after it
    appends its event, so a live write and a cold rebuild can never
    diverge. `"dispatched"` upserts a `Dispatch` row by item id.
    `"route"` replaces the item's `Route` row (latest-wins) and
    dirties whichever topics it touches: the
    topic the item is leaving, if it had a real (non-`NO_SIGNAL`)
    verdict before, and the topic it's joining, if the new verdict
    isn't `NO_SIGNAL` — a re-route dirties both the topic it left and
    the one it joins. Any other tag raises: the union grows a member
    per dispatch part, and a tag with no branch here means the ledger
    is ahead of the code, not that a verdict can be guessed.
    """
    # Captured before the checks below narrow `event` away: statically
    # the union is exhausted by them, but a ledger line is untyped JSON
    # at runtime and can carry anything.
    event_type: str = event["type"]
    if event["type"] == "dispatched":
        data = event["data"]
        session.merge(
            Dispatch(
                item_id=GraphQLId(data["item_id"]),
                session=data["session"],
                packet_chars=data["packet_chars"],
            )
        )
        return

    if event["type"] == "route":
        data = event["data"]
        item_id = GraphQLId(data["item_id"])
        previous = session.get(Route, item_id)
        if previous is not None and previous.verdict != NO_SIGNAL:
            _mark_topic_dirty(session, previous.verdict)
        session.merge(
            Route(
                item_id=item_id,
                verdict=data["verdict"],
                note=data["note"],
                session=data["session"],
            )
        )
        if data["verdict"] != NO_SIGNAL:
            _mark_topic_dirty(session, data["verdict"])
        return

    raise UnknownRoutingEventError(event_type)


def _mark_topic_dirty(session: Session, name: str) -> None:
    """Get-or-create the `Topic` named `name`, then mark it dirty."""
    topic = session.get(Topic, name)
    if topic is None:
        session.add(Topic(name=name, dirty=True))
    else:
        topic.dirty = True
