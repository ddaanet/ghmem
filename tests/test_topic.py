"""Test `topic.assemble`: routed-topic synthesis-context assembly."""

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from ghmem.aliases import IssueOrPRNumber
from ghmem.dispatch_store import CloseOutcome
from ghmem.exceptions import TopicNotRoutedError, UnknownSnapshotError
from ghmem.heat import HEAT_WEIGHTS
from ghmem.heat_query import heat_scores_for
from ghmem.ledger import FetchEvent, Ledger, RouteEvent, RoutingEvent
from ghmem.models import IssueOrPR, Topic
from ghmem.show import render
from ghmem.standing import build_standing
from ghmem.store import Store
from ghmem.topic import assemble

FIXTURE = Path(__file__).parent / "fixtures" / "heat_corpus.jsonl"
SHOW_FIXTURE = Path(__file__).parent / "fixtures" / "show_corpus.jsonl"


def _store(
    tmp_path: Path, fixture: Path = FIXTURE
) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a Store and its routing ledger, rebuilt from `fixture`."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(fixture)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


def _engine(tmp_path: Path) -> Engine:
    """Return a fresh engine on the mirror `_store` built under `tmp_path`."""
    return create_engine(f"sqlite:///{tmp_path / 'cache.sqlite'}")


def _route(
    store: Store, routing: Ledger[RoutingEvent], item_id: str, note: str
) -> None:
    """Route `item_id` to "softwrap" live, carrying `note`."""
    store.record_routing_event(
        routing,
        RouteEvent(
            type="route",
            data={
                "session": "s1",
                "item_id": item_id,
                "verdict": "softwrap",
                "note": note,
            },
        ),
    )


def test_assemble_header_carries_the_minted_token(tmp_path: Path) -> None:
    """The output's first line names a freshly minted 8-char base62 token.

    When: two heat_corpus items are routed to "softwrap" and assemble
      is called
    Then: the first line is `snapshot: <tok>`, `tok` is 8 base62
      characters, and the ledger's last event is a SnapshotEvent
      carrying that token and topic "softwrap"
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "relates to softwrap")
    _route(store, routing, "I_2", "also softwrap")

    output = assemble(store, routing, "softwrap")

    first_line = output.splitlines()[0]
    assert first_line.startswith("snapshot: ")
    token = first_line.removeprefix("snapshot: ")
    assert len(token) == 8
    assert all(c.isalnum() for c in token)

    last = list(routing)[-1]
    assert last["type"] == "snapshot"
    assert last["data"]["token"] == token
    assert last["data"]["topic"] == "softwrap"


def test_assemble_item_index_is_in_heat_order(tmp_path: Path) -> None:
    """Item-index `#N` lines appear in heat order, hottest first.

    When: I_1 (heavy activity) and I_2 (light activity) are both
      routed to "softwrap", which gives them different heat scores
    Then: the `#N` lines in the item index appear in the same order
      `heat_scores_for(HEAT_WEIGHTS, ...)` gives independently
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "note 1")
    _route(store, routing, "I_2", "note 2")

    output = assemble(store, routing, "softwrap")

    expected = [
        s.number for s in heat_scores_for(_engine(tmp_path), HEAT_WEIGHTS, [1, 2])
    ]
    assert expected[0] != expected[1]
    item_index = output.split("## Item index", 1)[1].split("## Threads", 1)[0]
    positions = [item_index.index(f"#{n} ") for n in expected]
    assert positions == sorted(positions)


def test_assemble_item_line_carries_note_and_counts(tmp_path: Path) -> None:
    """A routed item's line carries its routing note and its HeatScore counts.

    [mutation anchor: item index omits the routing note.]

    When: I_1 is routed to "softwrap" with a distinctive note
    Then: its item-index line contains that note text and its
      HeatScore's comment/reaction/participant/related counts
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "a distinctive routing note")

    output = assemble(store, routing, "softwrap")

    [score] = heat_scores_for(_engine(tmp_path), HEAT_WEIGHTS, [1])
    assert "note: a distinctive routing note" in output
    line = next(line for line in output.splitlines() if line.startswith("- #1 "))
    assert f"{score.comments} comments" in line
    assert f"{score.reactions} reactions" in line
    assert f"{score.participants} participants" in line
    assert f"{score.related_items} related" in line


def test_assemble_threads_carry_each_show_render(tmp_path: Path) -> None:
    """Each routed item's full `show.render` text lands in `## Threads`.

    When: I_1 and I_2 are routed to "softwrap"
    Then: for each, `show.render(store, standing, number)` is a
      substring of the assembled output's `## Threads` section
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "note 1")
    _route(store, routing, "I_2", "note 2")

    output = assemble(store, routing, "softwrap")

    standing = build_standing(store)
    threads = output.split("## Threads", 1)[1]
    for number in (1, 2):
        assert render(store, standing, IssueOrPRNumber(number)) in threads


def test_assemble_diffstat_for_a_pr(tmp_path: Path) -> None:
    """A routed PR with a fetched diff carries its diffstat segment.

    When: PR #2 (show_corpus's fixture PR, whose diff touches one file
      +10/-2) is routed to "softwrap"
    Then: its item-index line carries "1 files +10/-2"
    """
    store, routing = _store(tmp_path, SHOW_FIXTURE)
    _route(store, routing, "I_2", "ships feature X")

    output = assemble(store, routing, "softwrap")

    line = next(line for line in output.splitlines() if line.startswith("- #2 "))
    assert "1 files +10/-2" in line


def test_assemble_snapshot_digest_matches_recomputation(tmp_path: Path) -> None:
    """The recorded digest equals an independently recomputed sha256.

    [mutation anchors: digest drops `updated_at`; digest drops
    membership — either makes the independent recomputation disagree.]

    When: I_1 and I_2 are routed to "softwrap" and assemble is called
    Then: the SnapshotEvent's digest equals a sha256 computed here,
      independently of `dispatch_store._topic_digest`, over sorted
      `(item_id, updated_at.isoformat())` pairs of the routed items
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "note 1")
    _route(store, routing, "I_2", "note 2")

    assemble(store, routing, "softwrap")

    snapshot = next(e for e in routing if e["type"] == "snapshot")
    with Session(_engine(tmp_path)) as session:
        rows = session.execute(
            select(IssueOrPR.id, IssueOrPR.updated_at).where(
                IssueOrPR.id.in_(["I_1", "I_2"])
            )
        ).all()
    payload = "\n".join(
        f"{item_id}\t{updated_at.isoformat()}"
        for item_id, updated_at in sorted(rows, key=lambda row: row[0])
    )
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert snapshot["data"]["digest"] == expected


def test_assemble_empty_topic_raises(tmp_path: Path) -> None:
    """Assembling an unrouted topic raises and appends nothing.

    When: assemble is called for a topic name nothing was ever routed to
    Then: TopicNotRoutedError propagates and the ledger stays untouched
    """
    store, routing = _store(tmp_path)

    with pytest.raises(TopicNotRoutedError):
        assemble(store, routing, "nonexistent")

    assert list(routing) == []


def _token(output: str) -> str:
    """Extract the minted snapshot token from `assemble`'s first line."""
    return output.splitlines()[0].removeprefix("snapshot: ")


def test_close_clean_clears_dirty_and_appends_one_close_event(tmp_path: Path) -> None:
    """A clean close clears the topic's dirty flag and appends one CloseEvent.

    When: I_1 and I_2 are routed to "softwrap" and assemble mints a token
    Then: close_topic returns CloseOutcome("softwrap", cleared=True),
      the Topic row is dirty=False, and exactly one CloseEvent is
      appended to the routing ledger
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "note 1")
    _route(store, routing, "I_2", "note 2")
    token = _token(assemble(store, routing, "softwrap"))

    outcome = store.close_topic(routing, token)

    assert outcome == CloseOutcome("softwrap", cleared=True)
    with Session(_engine(tmp_path)) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=False)
    close_events = [e for e in routing if e["type"] == "close"]
    assert len(close_events) == 1
    assert close_events[0]["data"] == {"token": token, "topic": "softwrap"}


def test_close_stale_after_fresh_route_writes_nothing(tmp_path: Path) -> None:
    """A route landing after the snapshot makes the close stale.

    [mutation anchors: digest drops membership; close_topic appends on
    the stale path; close_topic compares the current digest against
    itself.]

    When: I_1 and I_2 are routed and assemble mints a token, then a
      third item, I_3, is routed to "softwrap" too, then close_topic
      is called with the (now stale) token
    Then: it returns cleared=False, the topic stays dirty, and no
      CloseEvent is appended
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "note 1")
    _route(store, routing, "I_2", "note 2")
    token = _token(assemble(store, routing, "softwrap"))
    _route(store, routing, "I_3", "note 3")

    outcome = store.close_topic(routing, token)

    assert outcome == CloseOutcome("softwrap", cleared=False)
    with Session(_engine(tmp_path)) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)
    assert all(e["type"] != "close" for e in routing)


def test_close_stale_after_activity_writes_nothing(tmp_path: Path) -> None:
    """A routed item's own activity after the snapshot also makes it stale.

    [mutation anchor: digest drops `updated_at`.]

    When: I_1 and I_2 are routed and assemble mints a token, then I_1's
      `updated_at` is bumped by a direct mirror UPDATE — standing in
      for a re-fetch that would otherwise require live GitHub activity
      — then close_topic is called with the (now stale) token
    Then: it returns cleared=False, the topic stays dirty, and no
      CloseEvent is appended
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "note 1")
    _route(store, routing, "I_2", "note 2")
    token = _token(assemble(store, routing, "softwrap"))
    engine = _engine(tmp_path)
    with Session(engine) as session:
        item = session.get(IssueOrPR, "I_1")
        assert item is not None
        item.updated_at = item.updated_at + timedelta(days=1)
        session.commit()

    outcome = store.close_topic(routing, token)

    assert outcome == CloseOutcome("softwrap", cleared=False)
    with Session(engine) as session:
        assert session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)
    assert all(e["type"] != "close" for e in routing)


def test_close_unknown_token_raises(tmp_path: Path) -> None:
    """Closing a token no snapshot ever minted raises and appends nothing.

    When: close_topic is called with a token nothing minted
    Then: UnknownSnapshotError propagates and the ledger stays untouched
    """
    store, routing = _store(tmp_path)

    with pytest.raises(UnknownSnapshotError):
        store.close_topic(routing, "nope")

    assert list(routing) == []


def test_close_clean_matches_a_cold_rebuild(tmp_path: Path) -> None:
    """A clean close's Topic row matches what replaying the ledger gives.

    [mutation anchor: reducer close branch no-ops — replay would leave
    the topic dirty.]

    Extends `test_routing_live.test_live_writes_match_a_cold_rebuild`
    to cover the close event: the live write and a cold rebuild from
    the ledger it produced must agree that the topic is clean.

    When: I_1 and I_2 are routed, assemble mints a token, and
      close_topic cleanly closes it
    Then: rebuilding the store from the ledger those writes produced
      leaves the Topic row dirty=False both live and rebuilt
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "note 1")
    _route(store, routing, "I_2", "note 2")
    token = _token(assemble(store, routing, "softwrap"))
    store.close_topic(routing, token)

    engine = _engine(tmp_path)
    with Session(engine) as session:
        live = session.get(Topic, "softwrap")

    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    store.rebuild_from_ledgers(fetch, routing)

    with Session(engine) as session:
        rebuilt = session.get(Topic, "softwrap")

    assert live == Topic(name="softwrap", dirty=False)
    assert live == rebuilt
