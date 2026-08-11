"""Test status: coverage report over routes, dispatches, topics, cursors."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.models import Topic
from ghmem.status import StatusSummary, build_status, render_status
from ghmem.store import Store

DB_NAME = "cache.sqlite"
FETCH_FIXTURE = Path(__file__).parent / "fixtures" / "status_fetch.jsonl"
ROUTING_FIXTURE = Path(__file__).parent / "fixtures" / "status_routing.jsonl"


def _build(tmp_path: Path, routing_path: Path, db_name: str = DB_NAME) -> StatusSummary:
    """Build a StatusSummary from the fetch fixture and `routing_path`."""
    store = Store.open(tmp_path / db_name)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(routing_path)
    store.rebuild_from_ledgers(fetch, routing)
    return build_status(store, fetch)


@pytest.fixture
def summary(tmp_path: Path) -> StatusSummary:
    """Return a StatusSummary built from the status fixture ledgers."""
    return _build(tmp_path, ROUTING_FIXTURE)


def test_counts_partition(summary: StatusSummary) -> None:
    """The four disjoint counts add up to the corpus total.

    `routed` and `no_signal` are deliberately UNEQUAL in the fixture:
    with both at one, swapping the two predicates produced an identical
    summary and the swap survived mutation.

    When: the fixture has two routed items, one no-signal item, one
      dispatched-unrouted item, and one untouched item
    Then: routed + no_signal + pending + dispatched == total, with the
      expected per-bucket counts
    """
    assert (
        summary.routed + summary.no_signal + summary.pending + summary.dispatched
        == summary.total
    )
    assert summary.total == 5
    assert summary.routed == 2
    assert summary.no_signal == 1
    assert summary.dispatched == 1
    assert summary.pending == 1


def test_routing_events_for_unfetched_items_do_not_count(
    summary: StatusSummary, tmp_path: Path
) -> None:
    """Routing events naming items absent from the corpus change no count.

    The two ledgers carry two different populations. `routing.jsonl` is
    original and outlives the regenerable `fetch.jsonl`, and a
    `fetch_failed` item writes no `IssueOrPR` row at all — so a routing
    id with no corpus row is the steady state, not corruption.

    Both ghosts route to the topic the fixture already has, keeping the
    assertion on the counts alone.

    When: the routing ledger gains a route and a dispatch for two item
      ids the fetch ledger never produced
    Then: the summary is unchanged — routed, no_signal, triaged,
      dispatched and pending all still describe the fetched population
    """
    ghosted_path = tmp_path / "ghosted_routing.jsonl"
    ghosted_path.write_text(
        ROUTING_FIXTURE.read_text(encoding="utf-8")
        + '{"type": "route", "data": {"session": "s1", "item_id": "I_ghost", '
        + '"verdict": "topic-a", "note": "routed after a corpus refetch", '
        + '"item_updated_at": "2026-01-01T00:00:00Z"}}\n'
        + '{"type": "dispatched", "data": {"session": "s1", '
        + '"item_id": "I_missing", "packet_chars": 90, "fit": "fit", '
        + '"diff_valve_dropped": 0}}\n',
        encoding="utf-8",
    )

    ghosted = _build(tmp_path, ghosted_path, db_name="ghosted.sqlite")

    assert ghosted == summary


def test_dirty_is_subset_of_topics(summary: StatusSummary) -> None:
    """Every dirty topic name is also a known topic.

    When: the fixture routes items to two real topics
    Then: dirty is a (non-empty) subset of topics
    """
    assert set(summary.dirty) <= set(summary.topics)
    assert summary.topics == ("topic-a", "topic-b")
    assert summary.dirty == ("topic-a", "topic-b")


def test_dirty_reports_only_the_topics_still_dirty(tmp_path: Path) -> None:
    """A topic cleaned by synthesis drops out of `dirty`, not of `topics`.

    A rebuild alone can never show this: replay starts from a dropped
    schema, so every topic it creates is dirty and "report them all"
    is indistinguishable from "report the dirty ones". Only a path
    that cleans a topic afterwards — Part C's close-out — separates
    them, so this test does the cleaning directly.

    When: one of two known topics is marked clean
    Then: `topics` still lists both and `dirty` lists only the other
    """
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FETCH_FIXTURE)
    store.rebuild_from_ledgers(fetch, Ledger(ROUTING_FIXTURE))
    with Session(create_engine(f"sqlite:///{tmp_path / DB_NAME}")) as session:
        session.get(Topic, "topic-a").dirty = False  # type: ignore[union-attr]
        session.commit()

    summary = build_status(store, fetch)

    assert summary.topics == ("topic-a", "topic-b")
    assert summary.dirty == ("topic-b",)


def test_cursor_reports_last_per_connection(summary: StatusSummary) -> None:
    """Two cursor events on one connection: the later hwm wins.

    When: connection "issues" has two cursor events in ledger order
    Then: cursors reports its later updated_at_hwm, not the earlier one
    """
    by_connection = dict(summary.cursors)

    assert by_connection["issues"] == "2026-01-05T00:00:00Z"
    assert by_connection["pulls"] == "2026-01-03T00:00:00Z"


def test_render_status_lists_topics_and_counts(summary: StatusSummary) -> None:
    """The rendered report carries every count, the topics and the dirty set.

    The dirty line is asserted on its own: with only a "topic-a appears
    somewhere" assertion, deleting the whole dirty line left the test
    green, because the topics line mentions it too.

    When: render_status is called on a built summary
    Then: the counts line, the topics line and the dirty line are all
      present, each naming what it should
    """
    output = render_status(summary)
    lines = output.splitlines()

    assert lines[0] == ("total=5 triaged=3 routed=2 no_signal=1 pending=1 dispatched=1")
    assert lines[1] == "topics: topic-a, topic-b"
    assert lines[2] == "dirty: topic-a, topic-b"
