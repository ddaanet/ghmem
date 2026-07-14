"""Test the SQLite mirror: rebuild-from-ledgers and the query API."""

from datetime import date, datetime
from pathlib import Path

import pytest

from ghmem.aliases import GraphQLId, JsonObject, Number
from ghmem.exceptions import IssueOrPRNotFoundError, PRDiffNotFoundError
from ghmem.ledger import FetchEvent, ItemEvent, Ledger
from ghmem.models import CrossRef, FileStat
from ghmem.store import RoutingEvent, Store

FIXTURE = Path(__file__).parent / "fixtures" / "fetch_small.jsonl"


def _rebuilt_store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from the small fixture ledger."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store


def test_rebuild_populates_tables(tmp_path: Path) -> None:
    """A rebuild replays every raw-node kind into its table.

    When: the store is rebuilt from a fixture ledger with items,
      comments, a review, a review comment, a reaction, a cross-ref, a
      commit, a PR diff, and a fetch-failed record
    Then: each corresponding query surfaces the replayed rows
    """
    store = _rebuilt_store(tmp_path)

    item = store.get_issue_or_pr(Number(1))
    pr = store.get_issue_or_pr(Number(2))

    assert store.comments_for(item.id)
    assert store.reviews_for(pr.id)
    review = store.reviews_for(pr.id)[0]
    assert store.review_comments_for(review.id)
    assert store.reactions_for(item.id)
    assert store.cross_refs_for(pr.id)
    assert store.commits()
    assert store.pr_diff_for(Number(2))
    failures = store.fetch_failures()
    assert [(f.id, f.reason) for f in failures] == [("I_999", "rate limited")]


def test_rebuild_idempotent(tmp_path: Path) -> None:
    """Rebuilding twice from the same ledgers doesn't double rows.

    When: the store is rebuilt twice in a row from the same fixture
    Then: querying afterwards returns the same single row per id, not
      duplicates
    """
    store = _rebuilt_store(tmp_path)
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")

    store.rebuild_from_ledgers(fetch, routing)

    assert len(store.commits()) == 1
    assert len(store.comments_for(GraphQLId("I_1"))) == 1
    assert len(store.issues_or_prs_authored_by("davida")) == 1


def test_get_issue_or_pr_and_relations(tmp_path: Path) -> None:
    """A rebuilt issue/PR carries its parsed fields and relations.

    When: issue/PR #2 (a merged pull request) is fetched after rebuild
    Then: its fields are parsed from the raw GraphQL node, and its
      review/cross-ref relations are reachable by id
    """
    store = _rebuilt_store(tmp_path)

    pr = store.get_issue_or_pr(Number(2))

    assert pr.id == GraphQLId("I_2")
    assert pr.kind == "pull_request"
    assert pr.state == "MERGED"
    assert pr.author == "davida"
    assert pr.merged_by == "maintainer"
    assert pr.merged_at == datetime.fromisoformat("2026-05-10T12:00:00+00:00")
    assert pr.author_association == "CONTRIBUTOR"

    reviews = store.reviews_for(pr.id)
    assert [r.state for r in reviews] == ["APPROVED"]
    assert store.review_comments_for(reviews[0].id)[0].path == "src/softwrap.go"

    assert store.cross_refs_for(pr.id) == [
        CrossRef(src_id=GraphQLId("I_2"), dst_id=GraphQLId("I_1"), kind="closes")
    ]


def test_merges_before_and_issues_or_prs_authored_by(tmp_path: Path) -> None:
    """merges_before and issues_or_prs_authored_by filter as specified.

    When: issue/PR #2 was merged 2026-05-10 and authored by "davida"
    Then: merges_before is exclusive of the merge date itself, and
      issues_or_prs_authored_by filters by exact author login
    """
    store = _rebuilt_store(tmp_path)

    assert store.merges_before(date(2026, 5, 10)) == []
    merged = store.merges_before(date(2026, 5, 11))
    assert [item.number for item in merged] == [Number(2)]

    authored = store.issues_or_prs_authored_by("davida")
    assert [item.number for item in authored] == [Number(2)]
    assert store.issues_or_prs_authored_by("nobody") == []


def test_null_author_maps_to_ghost(tmp_path: Path) -> None:
    """A deleted GitHub account (null author) parses as "ghost".

    When: a raw node carries `author: null` (a deleted account)
    Then: the parsed row's author is the literal "ghost", not None
    """
    store = Store.open(tmp_path / "cache.sqlite")
    node: JsonObject = {
        "id": "I_7",
        "__typename": "Issue",
        "number": 7,
        "title": "Filed by a since-deleted account",
        "state": "OPEN",
        "author": None,
        "createdAt": "2026-05-01T09:00:00Z",
        "updatedAt": "2026-05-01T09:00:00Z",
        "mergedBy": None,
        "mergedAt": None,
        "authorAssociation": None,
        "body": "orphaned",
    }
    event: ItemEvent = {"type": "item", "data": node}
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    fetch.append(event)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")

    store.rebuild_from_ledgers(fetch, routing)

    assert store.get_issue_or_pr(Number(7)).author == "ghost"


def test_get_issue_or_pr_missing_raises(tmp_path: Path) -> None:
    """Looking up an unknown issue/PR number raises.

    When: get_issue_or_pr is called with a Number absent from the corpus
    Then: IssueOrPRNotFoundError is raised, not None returned
    """
    store = _rebuilt_store(tmp_path)

    with pytest.raises(IssueOrPRNotFoundError):
        store.get_issue_or_pr(Number(999))


def test_pr_diff_roundtrip(tmp_path: Path) -> None:
    """A PR diff's file stats round-trip through SQLite.

    When: pr_diff_for is called for a PR with a diff in the fixture
    Then: its diffstat tuple matches the fixture's per-file counts
    """
    store = _rebuilt_store(tmp_path)

    diff = store.pr_diff_for(Number(2))

    assert diff.pr_number == Number(2)
    assert diff.truncated is False
    assert diff.diffstat == (
        FileStat(path="internal/display/softwrap.go", additions=42, deletions=5),
        FileStat(path="runtime/help/options.md", additions=3, deletions=0),
    )


def test_pr_diff_missing_raises(tmp_path: Path) -> None:
    """Looking up a diff for a non-PR issue raises.

    When: pr_diff_for is called for #1, an issue with no PR diff
    Then: PRDiffNotFoundError is raised, not None returned
    """
    store = _rebuilt_store(tmp_path)

    with pytest.raises(PRDiffNotFoundError):
        store.pr_diff_for(Number(1))
