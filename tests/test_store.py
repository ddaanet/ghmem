"""Test the SQLite mirror: rebuild-from-ledgers and the query API."""

from datetime import date, datetime
from pathlib import Path

import msgspec
import pytest

from ghmem.aliases import DiscussionNumber, GraphQLId, IssueOrPRNumber, JsonObject
from ghmem.exceptions import (
    DiscussionNotFoundError,
    IssueOrPRNotFoundError,
    PRDiffNotFoundError,
)
from ghmem.ingest import _RawIssueOrPR
from ghmem.ledger import FetchEvent, Ledger, RawNodeEvent
from ghmem.models import CrossRef, FileStat
from ghmem.store import RoutingEvent, Store

FIXTURE = Path(__file__).parent / "fixtures" / "fetch_small.jsonl"
DISCUSSION_FIXTURE = Path(__file__).parent / "fixtures" / "fetch_discussion.jsonl"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from the small fixture ledger."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store


@pytest.fixture
def discussion_store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from the discussion fixture ledger."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(DISCUSSION_FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store


def test_rebuild_populates_tables(store: Store) -> None:
    """A rebuild replays every raw-node kind into its table.

    When: the store is rebuilt from a fixture ledger with items,
      comments, a review, a review comment, a reaction, a cross-ref, a
      commit, a PR diff, and a fetch-failed record
    Then: each corresponding query surfaces the replayed rows
    """
    item = store.get_issue_or_pr(IssueOrPRNumber(1))
    pr = store.get_issue_or_pr(IssueOrPRNumber(2))

    assert store.comments_for(item.id)
    assert store.reviews_for(pr.id)
    review = store.reviews_for(pr.id)[0]
    assert store.review_comments_for(review.id)
    assert store.reactions_for(item.id)
    assert store.cross_refs_for(pr.id)
    assert store.commits()
    assert store.pr_diff_for(IssueOrPRNumber(2))
    failures = store.fetch_failures()
    assert [(f.id, f.reason) for f in failures] == [("I_999", "rate limited")]


def test_rebuild_idempotent(store: Store, tmp_path: Path) -> None:
    """Rebuilding twice from the same ledgers doesn't double rows.

    When: the store is rebuilt twice in a row from the same fixture
    Then: querying afterwards returns the same single row per id, not
      duplicates
    """
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")

    store.rebuild_from_ledgers(fetch, routing)

    assert len(store.commits()) == 1
    assert len(store.comments_for(GraphQLId("I_1"))) == 1
    assert len(store.issues_or_prs_authored_by("davida")) == 1


def test_get_issue_or_pr_and_relations(store: Store) -> None:
    """A rebuilt issue/PR carries its parsed fields and relations.

    When: issue/PR #2 (a merged pull request) is fetched after rebuild
    Then: its fields are parsed from the raw GraphQL node, and its
      review/cross-ref relations are reachable by id
    """
    pr = store.get_issue_or_pr(IssueOrPRNumber(2))

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


def test_merges_before_and_issues_or_prs_authored_by(store: Store) -> None:
    """merges_before and issues_or_prs_authored_by filter as specified.

    When: issue/PR #2 was merged 2026-05-10 and authored by "davida"
    Then: merges_before is exclusive of the merge date itself, and
      issues_or_prs_authored_by filters by exact author login
    """
    assert store.merges_before(date(2026, 5, 10)) == []
    merged = store.merges_before(date(2026, 5, 11))
    assert [item.number for item in merged] == [IssueOrPRNumber(2)]

    authored = store.issues_or_prs_authored_by("davida")
    assert [item.number for item in authored] == [IssueOrPRNumber(2)]
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
        "authorAssociation": "NONE",
        "body": "orphaned",
    }
    event: RawNodeEvent = {"type": "item", "data": node}
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    fetch.append(event)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")

    store.rebuild_from_ledgers(fetch, routing)

    assert store.get_issue_or_pr(IssueOrPRNumber(7)).author == "ghost"


def test_get_issue_or_pr_missing_raises(store: Store) -> None:
    """Looking up an unknown issue/PR number raises.

    When: get_issue_or_pr is called with a number absent from the corpus
    Then: IssueOrPRNotFoundError is raised, not None returned
    """
    with pytest.raises(IssueOrPRNotFoundError):
        store.get_issue_or_pr(IssueOrPRNumber(999))


def test_issue_or_pr_by_id(store: Store) -> None:
    """A cross-ref's GraphQLId resolves back to its issue/PR, or None.

    When: issue_or_pr_by_id is called with a known id and an unknown one
    Then: the known id resolves to the matching row, and the unknown id
      returns None rather than raising
    """
    pr = store.issue_or_pr_by_id(GraphQLId("I_2"))

    assert pr is not None
    assert pr.number == IssueOrPRNumber(2)
    assert pr.title == "Add softwrapcolumn option"

    assert store.issue_or_pr_by_id(GraphQLId("I_missing")) is None


def test_pr_diff_roundtrip(store: Store) -> None:
    """A PR diff's file stats round-trip through SQLite.

    When: pr_diff_for is called for a PR with a diff in the fixture
    Then: its diffstat tuple matches the fixture's per-file counts
    """
    diff = store.pr_diff_for(IssueOrPRNumber(2))

    assert diff.pr_number == IssueOrPRNumber(2)
    assert diff.truncated is False
    assert diff.diffstat == (
        FileStat(path="internal/display/softwrap.go", additions=42, deletions=5),
        FileStat(path="runtime/help/options.md", additions=3, deletions=0),
    )


def test_pr_diff_missing_raises(store: Store) -> None:
    """Looking up a diff for a non-PR issue raises.

    When: pr_diff_for is called for #1, an issue with no PR diff
    Then: PRDiffNotFoundError is raised, not None returned
    """
    with pytest.raises(PRDiffNotFoundError):
        store.pr_diff_for(IssueOrPRNumber(1))


def test_discussion_roundtrips_through_rebuild(discussion_store: Store) -> None:
    """A discussion and its comments round-trip through rebuild.

    When: a discussion, a top-level comment, a reply, and a reaction are
      replayed from the ledger
    Then: get_discussion returns the discussion, and
      discussion_comments_for returns both comments ordered by
      created_at, with the reply's parent_id pointing at the top-level
      comment
    """
    discussion = discussion_store.get_discussion(DiscussionNumber(1))

    assert discussion.id == GraphQLId("D_1")
    assert discussion.title == "How does softwrap interact with colorcolumn?"
    assert discussion.author == "octavia"
    assert discussion_store.reactions_for(discussion.id)

    comments = discussion_store.discussion_comments_for(discussion.id)
    assert [c.id for c in comments] == [GraphQLId("DC_1"), GraphQLId("DC_2")]
    assert comments[0].parent_id is None
    assert comments[1].parent_id == GraphQLId("DC_1")


def test_discussion_ghost_author(discussion_store: Store) -> None:
    """A deleted GitHub account (null author) parses as "ghost".

    When: a discussion node carries `author: null` (a deleted account)
    Then: the parsed row's author is the literal "ghost", not None
    """
    discussion = discussion_store.get_discussion(DiscussionNumber(2))

    assert discussion.author == "ghost"


def test_discussion_null_answer_and_category(discussion_store: Store) -> None:
    """An unanswered discussion has no answer id, and its category sticks.

    When: a discussion node carries `answer: null` and a category node
    Then: answer_id is None, and category stores the category node's name
    """
    discussion = discussion_store.get_discussion(DiscussionNumber(1))

    assert discussion.answer_id is None
    assert discussion.category == "Q&A"


def test_get_discussion_missing_raises(discussion_store: Store) -> None:
    """Looking up an unknown discussion number raises.

    When: get_discussion is called with a number absent from the corpus
    Then: DiscussionNotFoundError is raised, not None returned
    """
    with pytest.raises(DiscussionNotFoundError):
        discussion_store.get_discussion(DiscussionNumber(999))


def test_raw_node_rejects_unknown_key() -> None:
    """forbid_unknown_fields rejects a data object with a stray key.

    When: a raw node's data carries a key not declared on its struct
    Then: msgspec raises rather than silently ignoring it
    """
    node: JsonObject = {
        "id": "I_1",
        "__typename": "Issue",
        "number": 1,
        "title": "t",
        "state": "OPEN",
        "author": None,
        "createdAt": "2026-05-01T09:00:00Z",
        "updatedAt": "2026-05-01T09:00:00Z",
        "body": "b",
        "surpriseKey": "unexpected",
    }

    with pytest.raises(msgspec.ValidationError, match="surpriseKey"):
        _RawIssueOrPR.parse(node)
