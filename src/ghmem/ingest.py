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

from ghmem.aliases import JsonObject
from ghmem.ledger import FetchEvent, Ledger
from ghmem.models import Base, FetchFailed
from ghmem.raw_nodes import _RAW_BY_TYPE


def rebuild_from_ledgers(
    engine: Engine,
    fetch: Ledger[FetchEvent],
    routing: Ledger[RoutingEvent],
) -> None:
    """Drop and repopulate every table by replaying both ledgers.

    Replay is idempotent: the whole schema is dropped and recreated
    first, and every row is upserted by id, so rebuilding twice from
    the same ledgers leaves identical query results.

    `routing` is accepted for interface stability with subproject 2
    but not yet replayed — no `RoutingEvent` schema exists. HOOK: once
    routing events are defined, replay them here, after fetch.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for event in fetch:
            _replay_fetch_event(session, event)
        for _event in routing:
            pass  # no-op stub — see docstring HOOK above.
        session.commit()


# Placeholder until subproject 2 defines the real RoutingEvent schema. It
# deliberately doesn't invent routing event kinds — just a JSON object,
# so `rebuild_from_ledgers` has a concrete, mypy-strict-clean signature.
type RoutingEvent = JsonObject


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
