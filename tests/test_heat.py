"""Test heat: deterministic corpus ranking."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event

from ghmem.aliases import IssueOrPRNumber
from ghmem.heat import HEAT_WEIGHTS, rank
from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.models import HeatScore
from ghmem.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "heat_corpus.jsonl"


@contextmanager
def _recorded_statements() -> Iterator[list[str]]:
    """Record every SQL statement executed on any engine in the block."""
    statements: list[str] = []

    def record(  # noqa: ANN202
        _conn: Any,  # noqa: ANN401
        _cursor: Any,  # noqa: ANN401
        statement: str,
        _params: Any,  # noqa: ANN401
        _context: Any,  # noqa: ANN401
        _executemany: bool,  # noqa: FBT001
    ):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", record)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from the heat-ranking fixture ledger."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store


def _score_of(scores: list[HeatScore], number: int) -> HeatScore:
    """Return the score for issue/PR `number` out of a ranked list."""
    return next(s for s in scores if s.number == IssueOrPRNumber(number))


def test_rank_orders_hot_before_cold(store: Store) -> None:
    """The clearly-hottest item ranks first, the coldest ranks last.

    When: the corpus has one item with heavy comment/review/reaction
      activity across years, and two items with no activity at all
    Then: rank() puts the hot item first and a cold item last
    """
    scores = rank(store)

    assert scores[0].number == IssueOrPRNumber(1)
    assert scores[-1].number == IssueOrPRNumber(5)


def test_tie_break_is_number_ascending(store: Store) -> None:
    """Two items with an identical score order by number ascending.

    When: items #4 and #5 both have zero comments, reactions, and
      cross-refs, and a single author each
    Then: their scores are equal, and #4 sorts before #5
    """
    scores = rank(store)

    four = _score_of(scores, 4)
    five = _score_of(scores, 5)
    assert four.score == five.score
    assert scores.index(four) < scores.index(five)


def test_metrics_count_reviews_and_review_comments(store: Store) -> None:
    """The comments metric is total thread volume, not top-level only.

    When: item #1 has 2 top-level comments, 1 review, and 2 review
      comments on that review
    Then: its comments metric is 5, not 2
    """
    scores = rank(store)

    assert _score_of(scores, 1).comments == 5


def test_reactions_count_the_whole_thread(store: Store) -> None:
    """The reactions metric counts replies too, not just the body.

    Consensus in this repo often lands on a reply — a maintainer
    comments "I'd take a PR for this" and the thumbs go there, not on
    the original post. Scoping reactions to the body alone would rank
    exactly those threads cold, and would scope one metric differently
    from `comments` and `participants`, which already span the thread.

    Item #2 carries a reacted thread of its own so that a leak is
    visible: an uncorrelated subject subquery collects every item's
    replies, which would inflate #1 rather than leave it alone.

    When: item #1 carries 5 reactions on its body, 3 on a comment, 7 on
      a review and 2 on a review comment, while item #2 carries 2, 6,
      11 and 13 on the same four kinds
    Then: each item's metric sums its own thread only — 17 and 32
    """
    scores = rank(store)

    assert _score_of(scores, 1).reactions == 17
    assert _score_of(scores, 2).reactions == 32


def test_participants_are_distinct(store: Store) -> None:
    """A repeat author across roles counts once in participants.

    When: item #1's review and one of its review comments share the
      same author ("rev1")
    Then: participants counts the distinct-author set, not the raw
      count of (author, role) pairs
    """
    scores = rank(store)

    # octavia (item author), bea, ben (comments), rev1 (review + a
    # review comment, counted once), helper (the other review comment).
    assert _score_of(scores, 1).participants == 5


def test_related_items_counts_neighbours_not_edges(store: Store) -> None:
    """Two links between the same pair of items count as one neighbour.

    A PR that closes an issue is recorded twice by design: once from
    `closingIssuesReferences` as `closes`, once from the issue's
    timeline as `cross_referenced`. Probing the live API, this is not
    an edge case — 13 of 13 sampled closing references also appeared as
    timeline cross-references. Counting rows would therefore give every
    closing link twice the count of a bare mention, inflating exactly
    the issue↔PR pairs under the heaviest weight in the formula.

    When: items #1 and #3 are linked by both a `closes` row and a
      `cross_referenced` row
    Then: each counts the other once
    """
    scores = rank(store)

    assert _score_of(scores, 1).related_items == 1
    assert _score_of(scores, 3).related_items == 1


def test_cold_item_spans_zero_years(store: Store) -> None:
    """An item with no activity beyond its own filing spans zero years.

    When: item #4 has no comments, reviews, or reactions
    Then: its span_years is exactly 0.0
    """
    scores = rank(store)

    assert _score_of(scores, 4).span_years == 0.0


def test_rank_scores_the_corpus_in_one_query(store: Store) -> None:
    """Ranking aggregates in SQL, not by querying item by item.

    When: rank() runs over a five-item corpus
    Then: exactly one statement reaches the database, so the cost of
      ranking does not grow with the size of the corpus
    """
    with _recorded_statements() as statements:
        rank(store)

    assert len(statements) == 1, statements


def test_span_compares_instants_not_text(tmp_path: Path) -> None:
    """Activity recency is chronological, even across UTC offsets.

    When: an item's two comments are stored with different offsets, so
      that the lexicographically larger string is the earlier instant
    Then: span_years measures to the later instant, not the later
      string
    """
    ledger = tmp_path / "offsets.jsonl"
    ledger.write_text(
        '{"type": "item", "data": {"id": "I_1", "__typename": "Issue",'
        ' "number": 1, "title": "t", "state": "OPEN",'
        ' "author": {"login": "a"}, "createdAt": "2026-01-01T00:00:00Z",'
        ' "updatedAt": "2026-01-01T00:00:00Z", "mergedBy": null,'
        ' "mergedAt": null, "authorAssociation": "NONE", "body": ""}}\n'
        # Later string, earlier instant (2026-12-31T20:00Z).
        '{"type": "comment", "data": {"id": "C_1", "itemId": "I_1",'
        ' "author": {"login": "b"}, "createdAt": "2027-01-01T10:00:00+14:00",'
        ' "updatedAt": "2027-01-01T10:00:00+14:00",'
        ' "authorAssociation": "NONE", "body": ""}}\n'
        # Earlier string, later instant (2027-01-01T20:00Z).
        '{"type": "comment", "data": {"id": "C_2", "itemId": "I_1",'
        ' "author": {"login": "c"}, "createdAt": "2027-01-01T09:00:00-11:00",'
        ' "updatedAt": "2027-01-01T09:00:00-11:00",'
        ' "authorAssociation": "NONE", "body": ""}}\n'
    )
    store = Store.open(tmp_path / "offsets.sqlite")
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(Ledger(ledger), routing)

    scores = rank(store)

    assert scores[0].span_years == pytest.approx(365 / 365.25)


def test_heat_scores_for_filters_and_ranks(store: Store) -> None:
    """heat_scores_for returns only the named items, hottest first.

    When: the filter names cold #5, hot #1, and absent #999
    Then: only #1 and #5 come back, #1 (hotter) ahead of #5
    """
    scores = store.heat_scores_for(HEAT_WEIGHTS, [5, 1, 999])

    assert [s.number for s in scores] == [IssueOrPRNumber(1), IssueOrPRNumber(5)]


def test_heat_scores_for_empty_filter_returns_nothing(store: Store) -> None:
    """An empty filter scores nothing, rather than the whole corpus.

    When: heat_scores_for is called with no numbers
    Then: it returns an empty list
    """
    assert store.heat_scores_for(HEAT_WEIGHTS, []) == []
