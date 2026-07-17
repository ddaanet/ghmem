"""Tests for the issues and pull-requests sync driver."""

from collections import Counter
from pathlib import Path

import msgspec
import pytest
from pytest_mock import MockerFixture

from ghmem.aliases import GraphQLId, IssueOrPRNumber
from ghmem.exceptions import RateLimitError
from ghmem.github import GitHubClient
from ghmem.ledger import FetchEvent, Ledger
from ghmem.sync_core import _append_validated, _Sync
from ghmem.sync_items import _sync_issues, _sync_pull_requests
from ghmem.sync_wire import _IssuesQueryResponse, _PRsQueryResponse
from tests.sync_helpers import _ALL_PAGES, _ctx, _rebuild, _stub_graphql


def test_nested_comment_pagination(mocker: MockerFixture, tmp_path: Path) -> None:
    """A thread whose comments span two pages yields every comment.

    When: an item's first comments page has `hasNextPage: true`
    Then: sync issues a follow-up query and appends the second page's
      comment too, not just the first
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    _sync_issues(_ctx(mocker, fetch, _ALL_PAGES))

    store = _rebuild(fetch, tmp_path)

    comments = store.comments_for(GraphQLId("I_1"))
    assert [c.id for c in comments] == [GraphQLId("C_1"), GraphQLId("C_2")]


def test_resume_from_cursor(mocker: MockerFixture, tmp_path: Path) -> None:
    """A connection always continues from its last persisted cursor.

    When: the issues connection is interrupted by `RateLimitError`
      right after page 1 is fully drained and its cursor persisted,
      then re-run
    Then: the second call fetches only page 2 (via `after:
      "issues-cursor-1"`), and rebuilding finds both pages' items with
      no gaps or duplicates
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    interrupted_client = GitHubClient("tok")
    mocker.patch.object(
        interrupted_client,
        "graphql",
        side_effect=_stub_graphql(
            _ALL_PAGES, fail_at=(_IssuesQueryResponse, "issues-cursor-1")
        ),
    )

    with pytest.raises(RateLimitError):
        _sync_issues(_Sync(interrupted_client, fetch, Counter()))

    # Deliberately omits the page-1 key: if resume ever starts issues
    # over from `after=None` instead of the persisted cursor, the stub
    # raises KeyError instead of silently re-serving page 1.
    resumed_pages: dict[tuple[type, str | None], str] = {
        (_IssuesQueryResponse, "issues-cursor-1"): "issues_page2.json"
    }
    resumed_client = GitHubClient("tok")
    mocker.patch.object(
        resumed_client, "graphql", side_effect=_stub_graphql(resumed_pages)
    )

    _sync_issues(_Sync(resumed_client, fetch, Counter()))

    store = _rebuild(fetch, tmp_path)
    assert store.get_issue_or_pr(IssueOrPRNumber(1)).id == "I_1"
    assert store.get_issue_or_pr(IssueOrPRNumber(5)).id == "I_2"


def test_malformed_node_fails_before_append(tmp_path: Path) -> None:
    """A `data` dict violating the raw-node contract never reaches the ledger.

    When: `_append_validated` is called with an `item` event missing
      every required field but `id`
    Then: it raises instead of appending, and the ledger stays empty
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    ctx = _Sync(GitHubClient("tok"), fetch, Counter())

    with pytest.raises(msgspec.ValidationError):
        _append_validated(ctx, {"type": "item", "data": {"id": "I_1"}})

    assert list(fetch) == []


def test_null_node_recorded_fetch_failed(mocker: MockerFixture, tmp_path: Path) -> None:
    """A literal `null` list entry is skipped and recorded, not raised.

    When: the issues connection's second page carries a `null` node
      alongside a real one
    Then: sync appends a `fetch_failed` event for it and continues the
      page — the real item still lands
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_issues(_ctx(mocker, fetch, _ALL_PAGES))

    failures = [event["data"] for event in fetch if event["type"] == "fetch_failed"]
    assert len(failures) == 1
    assert "null node" in failures[0]["reason"]
    store = _rebuild(fetch, tmp_path)
    assert store.get_issue_or_pr(IssueOrPRNumber(5)).id == "I_2"


def test_truncated_nested_connection_records_fetch_failed(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A first-page-only connection past its limit is recorded, not lost.

    `reviews`, review comments, files, closing issues, and
    `timelineItems` are all taken as first-page-only by design; none of
    them are drained past `pageInfo`. Losing data silently there would
    defeat the point of the corpus, so an oversized one must at least
    be visible.

    When: a PR's `reviews` connection reports `hasNextPage: true` on
      its first page
    Then: sync records a `fetch_failed` naming the connection and node
      count, keyed by the PR id and connection name, and the sync still
      completes (the PR item itself is still appended)
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    pages = _ALL_PAGES | {(_PRsQueryResponse, None): "prs_page1_truncated_reviews.json"}

    _sync_pull_requests(_ctx(mocker, fetch, pages))

    failures = [event["data"] for event in fetch if event["type"] == "fetch_failed"]
    assert len(failures) == 1
    assert failures[0]["id"] == "I_3:reviews"
    assert failures[0]["reason"] == (
        "reviews truncated at first page (1 nodes, hasNextPage) on I_3"
    )
    items = [event["data"] for event in fetch if event["type"] == "item"]
    assert len(items) == 1


def test_pr_diff_appended(mocker: MockerFixture, tmp_path: Path) -> None:
    """A PR's diff combines GraphQL file stats with REST diff text.

    When: `_sync_pull_requests` drains a PR page
    Then: the `pr_diff` event carries per-file stats from GraphQL and
      `diffText`/`truncated` from the REST call
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_pull_requests(_ctx(mocker, fetch, _ALL_PAGES))

    diffs = [event["data"] for event in fetch if event["type"] == "pr_diff"]
    assert len(diffs) == 1
    assert diffs[0]["prNumber"] == 10
    assert diffs[0]["files"] == [
        {"path": "internal/display/softwrap.go", "additions": 42, "deletions": 5},
        {"path": "runtime/help/options.md", "additions": 3, "deletions": 0},
    ]
    assert diffs[0]["diffText"] == "diff --git a/x b/x\n"
    assert diffs[0]["truncated"] is False
