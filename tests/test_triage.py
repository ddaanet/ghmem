"""Test the triage session loop: mint, serve, gate, reserve, race.

`record_verdict`'s own rejection guards live in
`test_triage_verdict.py`, split out to keep each module under the
400-line cap. Three tests here touch `record_verdict`: two
serve-route-serve integrations that exercise the queue advancing, not
the guard (`test_record_verdict_then_serve_next_yields_the_next_item`
and `test_empty_queue_returns_triage_complete`), and
`test_reenter_lifecycle_end_to_end`, which drives the full
route/stale/reenter/re-route cycle and directly exercises the narrowed
B1 guard's stale-`Route`-acceptance branch — the fresh verdict from the
item's second `serve_next` call only goes through because its existing
`Route` has gone stale.
"""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from ghmem import triage
from ghmem.aliases import GraphQLId, IssueOrPRNumber
from ghmem.ledger import Ledger, RoutingEvent
from ghmem.models import IssueOrPR, Route, Topic
from tests.triage_helpers import (
    _content_only,
    _dispatch_row,
    _engine,
    _ranked_numbers,
    _store,
)

pytestmark = pytest.mark.usefixtures("target_corpus")


def test_fresh_session_serves_the_hottest_item(tmp_path: Path) -> None:
    """A fresh session's first packet is the hottest queued item.

    When: a fresh session token calls serve_next against an untouched
      queue
    Then: the packet carries the session header, a packet-chars
      header, and the show rendering of the hottest item — and no
      terminal message
    """
    store, routing = _store(tmp_path)
    hottest = _ranked_numbers(tmp_path)[0]
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token, tmp_path / "packets")

    assert result.message is None
    assert result.packet is not None
    lines = result.packet.splitlines()
    assert lines[0] == f"session: {token}"
    assert lines[1].startswith("packet-chars: ")
    assert f"item: {hottest}" in result.packet
    assert f"# #{hottest}" in result.packet  # show.render's own header


def test_record_verdict_then_serve_next_yields_the_next_item(tmp_path: Path) -> None:
    """Routing the served item writes the verdict and advances the queue.

    When: the hottest item is served, routed, and serve_next is called
      again under the same token
    Then: the item appears in store.routes() carrying the verdict and
      note, the routing ledger's last event is that route event, and
      the second packet names the second-hottest item, not the first
      again
    """
    store, routing = _store(tmp_path)
    first, second, _third = _ranked_numbers(tmp_path)
    token = triage.mint_session()

    first_result = triage.serve_next(store, routing, token, tmp_path / "packets")
    assert first_result.packet is not None
    assert f"item: {first}" in first_result.packet

    triage.record_verdict(store, routing, token, first, "some-topic", "reasoning")

    first_id = store.get_issue_or_pr(first).id
    routes_by_item = {route.item_id: route for route in store.routes()}
    assert routes_by_item[first_id].verdict == "some-topic"
    assert routes_by_item[first_id].note == "reasoning"
    last_event = list(routing)[-1]
    assert last_event == {
        "type": "route",
        "data": {
            "session": token,
            "item_id": first_id,
            "verdict": "some-topic",
            "note": "reasoning",
            "item_updated_at": store.get_issue_or_pr(first).updated_at.isoformat(),
        },
    }

    second_result = triage.serve_next(store, routing, token, tmp_path / "packets")

    assert second_result.packet is not None
    assert f"item: {second}" in second_result.packet
    assert f"item: {first}" not in second_result.packet


def test_item_cap_ends_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reaching BATCH_ITEMS ends the batch with a message, not a packet.

    When: BATCH_ITEMS is patched to 2
    Then: exactly two calls each serve a packet — the second carrying
      a Dispatch row — and the third returns the "budget exhausted"
      message with no packet, leaving the next item's Dispatch row
      unwritten
    """
    store, routing = _store(tmp_path)
    monkeypatch.setattr(triage, "BATCH_ITEMS", 2)
    _first, second, third = _ranked_numbers(tmp_path)
    token = triage.mint_session()

    first_result = triage.serve_next(store, routing, token, tmp_path / "packets")
    second_result = triage.serve_next(store, routing, token, tmp_path / "packets")
    result = triage.serve_next(store, routing, token, tmp_path / "packets")

    assert first_result.packet is not None
    assert second_result.packet is not None
    second_id = store.get_issue_or_pr(second).id
    assert _dispatch_row(tmp_path, second_id) is not None
    assert result.packet is None
    assert result.message == "batch complete: budget exhausted"
    third_id = store.get_issue_or_pr(third).id
    assert _dispatch_row(tmp_path, third_id) is None


def test_char_cap_ends_the_batch_mid_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exceeding BATCH_CHARS ends the batch, leaving the gating item queued.

    When: BATCH_CHARS is patched to fit exactly the first item served
      and nothing more
    Then: the second call returns the "budget exhausted" message with
      no packet, and the second item carries no Dispatch row
    """
    store, routing = _store(tmp_path)
    first, second, _third = _ranked_numbers(tmp_path)
    first_packet_chars = len(_content_only(store, first))
    monkeypatch.setattr(triage, "BATCH_CHARS", first_packet_chars + 1)
    token = triage.mint_session()

    first_result = triage.serve_next(store, routing, token, tmp_path / "packets")
    assert first_result.packet is not None

    result = triage.serve_next(store, routing, token, tmp_path / "packets")

    assert result.packet is None
    assert result.message == "batch complete: budget exhausted"
    second_id = store.get_issue_or_pr(second).id
    assert _dispatch_row(tmp_path, second_id) is None


def test_char_cap_serves_a_packet_that_exactly_fills_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A packet that exactly fills the remaining budget is still served.

    When: BATCH_CHARS is patched to exactly the first and second
      packets' combined size
    Then: the second call serves the second item rather than ending
      the batch — pinning the gate at `>` against the budget, not `>=`
    """
    store, routing = _store(tmp_path)
    first, second, _third = _ranked_numbers(tmp_path)
    first_packet_chars = len(_content_only(store, first))
    second_packet_chars = len(_content_only(store, second))
    monkeypatch.setattr(triage, "BATCH_CHARS", first_packet_chars + second_packet_chars)
    token = triage.mint_session()

    first_result = triage.serve_next(store, routing, token, tmp_path / "packets")
    assert first_result.packet is not None

    result = triage.serve_next(store, routing, token, tmp_path / "packets")

    assert result.packet is not None
    assert f"item: {second}" in result.packet


def test_first_item_served_even_when_it_exceeds_the_char_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session's first item is served whatever its size.

    When: BATCH_CHARS is patched far below any real packet's size
    Then: serve_next still serves the hottest item — the progress
      guarantee that keeps a single oversized item from deadlocking
      the queue
    """
    store, routing = _store(tmp_path)
    monkeypatch.setattr(triage, "BATCH_CHARS", 1)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token, tmp_path / "packets")

    assert result.packet is not None
    assert result.message is None


def test_empty_queue_returns_triage_complete(tmp_path: Path) -> None:
    """serve_next on a fully routed queue reports the corpus is done.

    When: every item in the fixture has been served and routed
    Then: the next serve_next call returns "triage complete: 0
      pending" verbatim — the message an orchestrator reads as "stop
      spawning sessions, the corpus is triaged"
    """
    store, routing = _store(tmp_path)
    numbers = _ranked_numbers(tmp_path)
    token = triage.mint_session()
    for number in numbers:
        served = triage.serve_next(store, routing, token, tmp_path / "packets")
        assert served.packet is not None
        triage.record_verdict(store, routing, token, number, "some-topic", "reasoning")

    result = triage.serve_next(store, routing, token, tmp_path / "packets")

    assert result.packet is None
    assert result.message == "triage complete: 0 pending"


def test_all_candidates_raced_away_returns_batch_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every candidate lost to a concurrent reservation ends this call.

    When: reserve_dispatch is forced to return False unconditionally,
      simulating every candidate being taken by other sessions between
      this call's snapshot and its reservations
    Then: serve_next returns "batch complete: all candidates taken",
      not the empty-queue string — the corpus still has pending work,
      just none this call could claim, so the orchestrator should keep
      spawning sessions rather than conclude the corpus is triaged
    """
    store, routing = _store(tmp_path)
    monkeypatch.setattr(store, "reserve_dispatch", lambda *_args, **_kwargs: False)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token, tmp_path / "packets")

    assert result.packet is None
    assert result.message == "batch complete: all candidates taken"


def test_race_lost_to_concurrent_reservation_serves_the_next_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate lost to a concurrent reservation is skipped, not fatal.

    When: the first candidate's reserve_dispatch call is forced to
      return False, simulating a concurrent session winning the race
      for it
    Then: serve_next renders and reserves the second-hottest candidate
      instead, and the first candidate carries no Dispatch row
    """
    store, routing = _store(tmp_path)
    first, second, _third = _ranked_numbers(tmp_path)
    real_reserve = store.reserve_dispatch
    calls = []

    def fake_reserve(routing_arg: Ledger[RoutingEvent], event: object) -> bool:
        calls.append(event)
        if len(calls) == 1:
            return False
        return real_reserve(routing_arg, event)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "reserve_dispatch", fake_reserve)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token, tmp_path / "packets")

    assert result.packet is not None
    assert f"item: {second}" in result.packet
    assert len(calls) == 2
    first_id = store.get_issue_or_pr(first).id
    assert _dispatch_row(tmp_path, first_id) is None


def test_mint_session_draws_eight_chars_from_the_expected_alphabet() -> None:
    """mint_session returns an 8-character token over [0-9A-Za-z].

    When: mint_session is called
    Then: the token is 8 characters long and every character is a
      digit or an ASCII letter
    """
    token = triage.mint_session()

    assert len(token) == 8
    assert all(c.isalnum() and c.isascii() for c in token)


def test_reenter_lifecycle_end_to_end(tmp_path: Path) -> None:
    """A stale route flows all the way back through triage to a fresh verdict.

    Exercises the spec's full "Data flow, end to end": route, corpus
    moves, stale_routes + reenter free it, it rejoins the untriaged
    queue, gets served again, and a fresh verdict closes the loop with
    an up-to-date stamp.

    When: item #1 is dispatched and routed to "softwrap" (items #2 and
      #3 routed away too, so #1 is the only untriaged item), its live
      updated_at moves forward, stale_routes + a reenter event free
      it, and it's served and routed again
    Then: it carries a fresh item_updated_at, "softwrap" is dirty, and
      stale_routes reports nothing more for it
    """
    store, routing = _store(tmp_path)
    for other_id, verdict in (("I_2", "no-signal"), ("I_3", "other-topic")):
        store.record_routing_event(
            routing,
            {
                "type": "route",
                "data": {
                    "session": "s0",
                    "item_id": other_id,
                    "verdict": verdict,
                    "note": "n",
                    "item_updated_at": "2026-01-01T00:00:00Z",
                },
            },
        )
    store.record_routing_event(
        routing,
        {
            "type": "dispatched",
            "data": {
                "session": "s0",
                "item_id": "I_1",
                "packet_chars": 100,
                "fit": "fit",
                "diff_valve_dropped": 0,
            },
        },
    )
    store.record_routing_event(
        routing,
        {
            "type": "route",
            "data": {
                "session": "s0",
                "item_id": "I_1",
                "verdict": "softwrap",
                "note": "initial",
                "item_updated_at": "2026-01-01T00:00:00Z",
            },
        },
    )
    with Session(_engine(tmp_path)) as session:
        session.get_one(
            IssueOrPR, GraphQLId("I_1")
        ).updated_at = datetime.fromisoformat("2026-01-05T00:00:00Z")
        session.commit()

    stale = store.stale_routes()
    assert [r.item_id for r in stale] == [GraphQLId("I_1")]
    for route in stale:
        store.record_routing_event(
            routing, {"type": "reenter", "data": {"item_id": route.item_id}}
        )

    token = triage.mint_session()
    served = triage.serve_next(store, routing, token, tmp_path / "packets")
    assert served.packet is not None
    assert "item: 1" in served.packet

    triage.record_verdict(
        store, routing, token, IssueOrPRNumber(1), "softwrap", "still softwrap"
    )

    with Session(_engine(tmp_path)) as session:
        route = session.get(Route, GraphQLId("I_1"))
        topic = session.get(Topic, "softwrap")
    assert route is not None
    assert route.item_updated_at == datetime.fromisoformat("2026-01-05T00:00:00Z")
    assert topic is not None
    assert topic.dirty is True
    assert store.stale_routes() == []
