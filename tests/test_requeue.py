"""Test `requeue`: freeing dispatches a crashed session never routed."""

from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId
from ghmem.cli import main
from ghmem.ledger import FetchEvent, Ledger, RequeueEvent, RoutingEvent
from ghmem.models import Dispatch, Route, Topic
from ghmem.store import Store

DB_NAME = "cache.sqlite"
FETCH_FIXTURE = Path(__file__).parent / "fixtures" / "routing_fetch.jsonl"
ROUTING_FIXTURE = Path(__file__).parent / "fixtures" / "routing_events.jsonl"

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
DISPATCH_2: RoutingEvent = {
    "type": "dispatched",
    "data": {
        "session": "s2",
        "item_id": "I_2",
        "packet_chars": 100,
        "fit": "fit",
        "diff_valve_dropped": 0,
    },
}
ROUTE_1: RoutingEvent = {
    "type": "route",
    "data": {
        "session": "s1",
        "item_id": "I_1",
        "verdict": "some-topic",
        "note": "n",
        "item_updated_at": "2026-01-01T00:00:00Z",
    },
}
REQUEUE_1: RequeueEvent = {
    "type": "requeue",
    "data": {"session": "s1", "item_id": "I_1"},
}
REQUEUE_2: RequeueEvent = {
    "type": "requeue",
    "data": {"session": "s2", "item_id": "I_2"},
}
REQUEUE_NEVER_DISPATCHED: RequeueEvent = {
    "type": "requeue",
    "data": {"session": "s9", "item_id": "I_2"},
}


def _live_store(tmp_path: Path) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a store seeded with the fetch fixture and an empty routing log."""
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


def _session(tmp_path: Path) -> Session:
    """Open a second session onto the same SQLite file the store uses."""
    return Session(create_engine(f"sqlite:///{tmp_path / DB_NAME}"))


def _corpus_session(corpus: Path) -> Session:
    """Open a session onto the SQLite mirror inside `corpus`."""
    return Session(create_engine(f"sqlite:///{corpus / DB_NAME}"))


@pytest.fixture
def corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point $GHMEM_CORPUS_DIR at a rebuilt corpus.

    Seeded with the routing fixture.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FETCH_FIXTURE.read_text())
    (corpus / "routing.jsonl").write_text(ROUTING_FIXTURE.read_text())
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    assert CliRunner().invoke(main, ["rebuild"]).exit_code == 0
    return corpus


def test_requeue_deletes_the_dispatch_row(tmp_path: Path) -> None:
    """Requeuing a stranded dispatch removes its Dispatch row.

    Without the delete, `dispatched_unrouted` would still see the item
    as outstanding and it would never be served again — the failure
    mode Part A's `Dispatch` docstring calls out.

    When: item #1 is dispatched, then requeued
    Then: no Dispatch row survives for it
    """
    store, routing = _live_store(tmp_path)
    store.record_routing_event(routing, DISPATCH_1)

    store.record_routing_event(routing, REQUEUE_1)

    with _session(tmp_path) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) is None


def test_requeue_command_leaves_routed_items_alone(corpus: Path) -> None:
    """`ghmem requeue` only frees dispatches with no current Route.

    The routing fixture dispatches and routes #1 and #2, and dispatches
    #3 without routing it — #3 alone is stranded.

    When: `ghmem requeue` runs over that ledger
    Then: #3's Dispatch row is gone, but #1's and #2's survive
    """
    result = CliRunner().invoke(main, ["requeue"])

    assert result.exit_code == 0, result.output
    with _corpus_session(corpus) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) is not None
        assert session.get(Dispatch, GraphQLId("I_2")) is not None
        assert session.get(Dispatch, GraphQLId("I_3")) is None


def test_requeue_naming_an_undispatched_item_replays_cleanly(
    tmp_path: Path,
) -> None:
    """A requeue for an item with no Dispatch row is a no-op, not an error.

    Deleting an already-absent row keeps replay idempotent: a requeue
    the orchestration script emits for an item whose row is already
    gone (e.g. a re-run) must not raise.

    When: item #2, never dispatched, is requeued
    Then: recording it raises nothing, and a rebuild from the resulting
      ledger raises nothing either
    """
    store, routing = _live_store(tmp_path)

    store.record_routing_event(routing, REQUEUE_NEVER_DISPATCHED)

    with _session(tmp_path) as session:
        assert session.get(Dispatch, GraphQLId("I_2")) is None
    store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)
    with _session(tmp_path) as session:
        assert session.get(Dispatch, GraphQLId("I_2")) is None


def test_live_requeue_matches_a_cold_rebuild(tmp_path: Path) -> None:
    """Recording a requeue live leaves exactly what replaying it would.

    Same claim `test_routing_live.py` proves for dispatch and route:
    the live path and `rebuild_from_ledgers` share one reducer, so a
    requeue cannot drift between the two.

    The scenario mixes a surviving route with a requeue so the
    comparison is over non-empty tables — #1 is dispatched and routed
    (its Route, Dispatch, and Topic rows persist), #2 is dispatched
    then requeued (its Dispatch row goes). Comparing all-empty tuples
    would pass whether or not the reducer agreed with itself.

    When: that mix is recorded live, then the store is rebuilt from the
      ledger those writes produced
    Then: Route, Dispatch, and Topic rows are identical either way, and
      #2's Dispatch is the one that is gone
    """
    store, routing = _live_store(tmp_path)
    for event in (DISPATCH_1, ROUTE_1, DISPATCH_2, REQUEUE_2):
        store.record_routing_event(routing, event)

    with _session(tmp_path) as session:
        live = (
            list(session.execute(select(Route)).scalars()),
            list(session.execute(select(Dispatch)).scalars()),
            list(session.execute(select(Topic)).scalars()),
        )

    store.rebuild_from_ledgers(Ledger(FETCH_FIXTURE), routing)

    with _session(tmp_path) as session:
        rebuilt = (
            list(session.execute(select(Route)).scalars()),
            list(session.execute(select(Dispatch)).scalars()),
            list(session.execute(select(Topic)).scalars()),
        )

    assert live == rebuilt
    routes, dispatches, topics = live
    assert routes, "expected a surviving Route to compare, not an empty table"
    assert dispatches, "expected a surviving Dispatch to compare, not an empty table"
    assert topics, "expected a dirtied Topic to compare, not an empty table"
    dispatch_ids = {d.item_id for d in dispatches}
    assert GraphQLId("I_2") not in dispatch_ids
    assert GraphQLId("I_1") in dispatch_ids


def test_requeue_command_prints_the_count_and_exits_zero(corpus: Path) -> None:
    """`ghmem requeue` reports how many items it freed.

    Its caller is the orchestration script, run before any triage
    session exists — the count is what tells it whether the last run
    left anything stranded.

    When: `ghmem requeue` runs over a ledger with one stranded item
    Then: it exits 0 and prints that count
    """
    result = CliRunner().invoke(main, ["requeue"])

    assert result.exit_code == 0, result.output
    assert result.output == "Requeued 1 item(s)\n"


def test_requeue_command_frees_items_from_every_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem requeue` frees every stranded item, keyed to its own session.

    Two items stranded under two different sessions exercise what a
    single-item ledger cannot: that the count is the real number freed
    (not a hard-coded 1), and that each emitted event carries the
    session it abandoned — the signal subproject 4 reads to notice a
    batch budget set too high, and a field the reducer never consumes,
    so nothing else would catch it being dropped.

    When: #1 is stranded under `s1` and #2 under `s2`, and `requeue`
      runs
    Then: it reports two freed, and the ledger gains one requeue event
      per item, each carrying that item's own session
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FETCH_FIXTURE.read_text())
    routing_path = corpus / "routing.jsonl"
    routing: Ledger[RoutingEvent] = Ledger(routing_path)
    routing.append(DISPATCH_1)
    routing.append(DISPATCH_2)
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    assert CliRunner().invoke(main, ["rebuild"]).exit_code == 0

    result = CliRunner().invoke(main, ["requeue"])

    assert result.exit_code == 0, result.output
    assert result.output == "Requeued 2 item(s)\n"
    requeued: Ledger[RoutingEvent] = Ledger(routing_path)
    carried = {
        (event["data"]["item_id"], event["data"]["session"])
        for event in requeued
        if event["type"] == "requeue"
    }
    assert carried == {("I_1", "s1"), ("I_2", "s2")}
