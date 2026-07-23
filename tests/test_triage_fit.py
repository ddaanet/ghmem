"""Test the fit path: what happens when a session's first item doesn't fit.

The rest of the session loop lives in `test_triage.py`; this module
covers only `triage._fit_first_item` and the branch in `serve_next`
that calls it — a session's first item whose packet exceeds
`BATCH_CHARS`.
"""

from pathlib import Path

import pytest

from ghmem import triage
from ghmem.aliases import IssueOrPRNumber
from ghmem.ledger import DispatchedData, FetchEvent, Ledger, RoutingEvent
from ghmem.show import render
from ghmem.show_diff import DIFF_BODY_MAX_CHARS, _cut_diff_body
from ghmem.standing import build_standing
from ghmem.store import Store
from tests.diff_fixtures import (
    DIFF_HEADER_LINE,
    LINE_WIDTH,
    PR_BODY_MARKER,
    big_diff_text,
    diff_body_line,
    write_diff_corpus,
    write_two_pr_diff_corpus,
)
from tests.triage_helpers import _content_only, _ranked_numbers, _store

_DIFF_LINES = 500

_VALVE_DIFF_LINES = DIFF_BODY_MAX_CHARS // LINE_WIDTH + 100
"""Diff lines that overrun `DIFF_BODY_MAX_CHARS` by a comfortable margin.

Derived from the valve rather than hardcoded, so raising the valve cannot leave
this fixture sitting under it and the valve test passing vacuously.
"""


def _diff_session(
    tmp_path: Path, diff_lines: int
) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a Store and routing ledger rebuilt from a generated diff corpus.

    Wraps `write_diff_corpus` so each fit-path test starts from a
    fresh, single-PR store sized by `diff_lines`, without repeating
    the corpus-then-rebuild boilerplate `tests.diff_fixtures` leaves
    to its callers.
    """
    corpus = tmp_path / "diff_fetch.jsonl"
    write_diff_corpus(corpus, diff_lines=diff_lines)
    store = Store.open(tmp_path / "diff_cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(corpus)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


def _dispatched(routing: Ledger[RoutingEvent]) -> list[DispatchedData]:
    """Return every dispatched event's data from `routing`, in append order.

    An explicit loop, not a comprehension: the loop is what narrows
    the `RoutingEvent` union on its `type` discriminant under mypy
    strict — a comprehension leaves the element type unnarrowed and
    `fit` unreachable.
    """
    dispatched: list[DispatchedData] = []
    for event in routing:
        if event["type"] == "dispatched":
            dispatched.append(event["data"])  # noqa: PERF401
    return dispatched


def _floor_chars(store: Store, number: IssueOrPRNumber) -> int:
    r"""Return `number`'s packet size with its diff body removed.

    Built from `show.render(..., diff_max_chars=0)` and the
    `item: {number}\\n` line directly, not by calling `triage`'s
    private renderer, so this is an independent measure of the floor
    rather than a restatement of the formula under test.
    """
    standing = build_standing(store)
    body = render(store, standing, number, diff_max_chars=0)
    return len(f"item: {number}\n{body}")


def _full_chars(store: Store, number: IssueOrPRNumber) -> int:
    r"""Return `number`'s full packet size at the default diff valve.

    Built the same way `_floor_chars` is — from `show.render` and the
    `item: {number}\\n` line directly, not by calling `triage`'s
    private renderer — so it's an independent measure of the fit
    path's entry boundary rather than a restatement of the formula
    under test.
    """
    standing = build_standing(store)
    body = render(store, standing, number)
    return len(f"item: {number}\n{body}")


def test_packet_within_budget_records_fit_and_carries_no_note(tmp_path: Path) -> None:
    """A packet under budget is tagged fit and carries no note.

    When: the first item of a session is served under the real budget
    Then: its dispatched event carries fit="fit" and the packet has no
      packet-note: header at all
    """
    store, routing = _diff_session(tmp_path, diff_lines=10)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token)

    assert result.packet is not None
    assert "packet-note:" not in result.packet
    dispatched = _dispatched(routing)
    assert dispatched[-1]["fit"] == "fit"
    assert dispatched[-1]["diff_valve_dropped"] == 0


def test_dispatch_records_what_the_diff_valve_dropped(tmp_path: Path) -> None:
    """The valve firing is recorded even on a packet well inside budget.

    This is the case `fit` alone cannot report: the packet is nowhere
    near `BATCH_CHARS`, so it is tagged an unremarkable "fit", while
    `DIFF_BODY_MAX_CHARS` quietly removed several thousand characters
    of diff body. Both constants are calibrated in subproject 4, so
    the valve needs its own signal against the items actually served.

    When: a session's first item carries a diff longer than the valve,
      and its whole packet still fits the batch budget
    Then: the dispatched event is tagged fit, and its
      diff_valve_dropped equals the drop computed independently from
      the fixture generator and `show_diff._cut_diff_body`
    """
    store, routing = _diff_session(tmp_path, diff_lines=_VALVE_DIFF_LINES)
    full = big_diff_text(_VALVE_DIFF_LINES)
    kept, _cut = _cut_diff_body(full, DIFF_BODY_MAX_CHARS)
    expected = len(full) - len(kept)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token)

    # Without this the fixture could sit under the valve and the
    # assertion below would pass on a hardcoded zero.
    assert expected > 0
    assert result.packet is not None
    dispatched = _dispatched(routing)
    assert dispatched[-1]["fit"] == "fit"
    assert dispatched[-1]["diff_valve_dropped"] == expected


def test_packet_exactly_at_budget_is_fit_not_diff_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A packet exactly at budget still fits — the fit path isn't entered.

    When: BATCH_CHARS is patched to exactly the first item's full
      packet size at the default valve, measured independently via
      show.render rather than through the fit path
    Then: the dispatched event is tagged fit, not diff_cut, and the
      packet carries no packet-note: header — a packet at the budget
      is within it, not over it, so entering the fit path here would
      credit a truncation that never happened
    """
    store, routing = _diff_session(tmp_path, diff_lines=_DIFF_LINES)
    full = _full_chars(store, IssueOrPRNumber(1))
    monkeypatch.setattr(triage, "BATCH_CHARS", full)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token)

    assert result.packet is not None
    assert "packet-note:" not in result.packet
    dispatched = _dispatched(routing)
    assert dispatched[-1]["fit"] == "fit"


def test_oversized_first_item_has_its_diff_cut_to_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first item whose full packet exceeds budget has its diff cut to fit.

    When: BATCH_CHARS is patched to the floor plus an allowance that
      lands one character short of a real line boundary in a 500-line
      generated diff — chosen so a one-character overshoot in the
      allowance pulls in a whole extra line
    Then: the dispatched event is tagged diff_cut, its packet_chars is
      at or under the budget — "fits by construction" asserted, not
      assumed — the packet carries the diff-cut note, the file-list
      line for src/big.go survives, and the truncation marker is
      present
    """
    store, routing = _diff_session(tmp_path, diff_lines=_DIFF_LINES)
    floor = _floor_chars(store, IssueOrPRNumber(1))
    allowance = len(DIFF_HEADER_LINE) + 10 * LINE_WIDTH - 1
    budget = floor + allowance
    monkeypatch.setattr(triage, "BATCH_CHARS", budget)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token)

    assert result.packet is not None
    assert result.message is None
    dispatched = _dispatched(routing)
    assert dispatched[-1]["fit"] == "diff_cut"
    assert dispatched[-1]["packet_chars"] <= budget
    assert "packet-note: diff truncated to fit the batch budget" in result.packet
    assert "src/big.go" in result.packet
    assert "… [diff truncated] …" in result.packet


def test_diff_cut_packet_fills_the_allowance_to_the_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diff_cut packet uses every character of its allowance, not fewer.

    When: BATCH_CHARS is patched to the floor plus an allowance that
      lands exactly on a real line boundary in a 500-line generated
      diff — unlike the test above, which lands one character short
    Then: packet_chars equals the budget exactly, pinning that the
      allowance is body space with nothing held back "for the
      separator" — subtracting a character there would cut at the
      previous boundary instead and drop a whole 40-char line short of
      the budget
    """
    store, routing = _diff_session(tmp_path, diff_lines=_DIFF_LINES)
    floor = _floor_chars(store, IssueOrPRNumber(1))
    allowance = len(DIFF_HEADER_LINE) + 10 * LINE_WIDTH
    budget = floor + allowance
    monkeypatch.setattr(triage, "BATCH_CHARS", budget)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token)

    assert result.packet is not None
    dispatched = _dispatched(routing)
    assert dispatched[-1]["fit"] == "diff_cut"
    assert dispatched[-1]["packet_chars"] == budget


def test_uncuttable_first_item_is_served_oversized_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first item whose floor alone exceeds budget is served oversized.

    When: BATCH_CHARS is patched below even the floor render
    Then: the item is still served — the queue must advance —
      result.message is None, the dispatched event is tagged
      oversized with its packet_chars pinned to the floor (and over
      budget), the packet carries the oversized note, the file list
      survives, and no generated body line appears
    """
    store, routing = _diff_session(tmp_path, diff_lines=_DIFF_LINES)
    floor = _floor_chars(store, IssueOrPRNumber(1))
    budget = floor - 1
    monkeypatch.setattr(triage, "BATCH_CHARS", budget)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token)

    assert result.packet is not None
    assert result.message is None
    dispatched = _dispatched(routing)
    assert dispatched[-1]["fit"] == "oversized"
    assert dispatched[-1]["packet_chars"] == floor
    assert dispatched[-1]["packet_chars"] > budget
    assert (
        "packet-note: served without its diff body; thread text and file "
        "list leave no budget for it" in result.packet
    )
    assert "src/big.go" in result.packet
    assert diff_body_line(0) not in result.packet


def test_zero_allowance_is_still_served_oversized_not_diff_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A budget landing exactly on the floor is oversized, not diff_cut.

    When: BATCH_CHARS is patched to exactly the floor render's size,
      leaving zero characters of allowance for the diff body
    Then: the dispatched event is tagged oversized, not diff_cut — a
      zero allowance cuts nothing, so tagging it a cut would credit a
      truncation that never happened
    """
    store, routing = _diff_session(tmp_path, diff_lines=_DIFF_LINES)
    floor = _floor_chars(store, IssueOrPRNumber(1))
    monkeypatch.setattr(triage, "BATCH_CHARS", floor)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token)

    assert result.packet is not None
    dispatched = _dispatched(routing)
    assert dispatched[-1]["fit"] == "oversized"


def test_thread_text_is_never_cut_on_the_oversized_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thread text survives even under the tightest oversized budget.

    When: the first item is served oversized under a budget of 1
    Then: the PR's distinctive body sentence appears in the packet in
      full — thread text is never a truncation target on any path
    """
    store, routing = _diff_session(tmp_path, diff_lines=_DIFF_LINES)
    monkeypatch.setattr(triage, "BATCH_CHARS", 1)
    token = triage.mint_session()

    result = triage.serve_next(store, routing, token)

    assert result.packet is not None
    assert PR_BODY_MARKER in result.packet


def test_a_later_item_never_enters_the_fit_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later item that doesn't fit is excluded, never truncated.

    When: BATCH_CHARS is patched to fit the first item and nothing
      more, and a second serve is attempted under the same session
      token
    Then: the batch ends with packet is None and
      message == "batch complete: budget exhausted" rather than
      serving a cut second packet, and every dispatched event written
      stays tagged fit
    """
    store, routing = _store(tmp_path)
    first, _second, _third = _ranked_numbers(tmp_path)
    first_packet_chars = len(_content_only(store, first))
    monkeypatch.setattr(triage, "BATCH_CHARS", first_packet_chars + 1)
    token = triage.mint_session()

    first_result = triage.serve_next(store, routing, token)
    assert first_result.packet is not None

    result = triage.serve_next(store, routing, token)

    assert result.packet is None
    assert result.message == "batch complete: budget exhausted"
    assert all(data["fit"] == "fit" for data in _dispatched(routing))


def test_a_later_item_too_big_for_its_own_packet_stays_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later item whose own packet exceeds budget is excluded, not fit.

    When: a two-PR corpus has PR #1 small and PR #2 large enough that
      PR #2's own packet at the default valve exceeds BATCH_CHARS, and
      the first item is served before BATCH_CHARS is patched to one
      character short of PR #2's own packet size
    Then: the second serve ends the batch with packet None and
      message == "batch complete: budget exhausted", and only one
      dispatched event exists — PR #2 is discarded by the ordinary char
      gate rather than entering the fit path and being served with its
      diff cut, which is a first-item-only privilege
    """
    corpus = tmp_path / "diff_fetch.jsonl"
    write_two_pr_diff_corpus(corpus, small_diff_lines=1, large_diff_lines=_DIFF_LINES)
    store = Store.open(tmp_path / "diff_cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(corpus)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    second = IssueOrPRNumber(2)
    budget = _full_chars(store, second) - 1
    monkeypatch.setattr(triage, "BATCH_CHARS", budget)
    token = triage.mint_session()

    first_result = triage.serve_next(store, routing, token)
    assert first_result.packet is not None

    result = triage.serve_next(store, routing, token)

    assert result.packet is None
    assert result.message == "batch complete: budget exhausted"
    assert len(_dispatched(routing)) == 1
