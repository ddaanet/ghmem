"""Test the resumable GraphQL sync driver: issues, PRs, commits."""

import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import msgspec
import pytest
from pytest_mock import MockerFixture

from ghmem.aliases import GraphQLId, IssueOrPRNumber, JsonObject
from ghmem.exceptions import RateLimitError
from ghmem.github import GitHubClient
from ghmem.ledger import FetchEvent, Ledger
from ghmem.store import RoutingEvent, Store
from ghmem.sync import (
    _append_validated,
    _CommitHistoryQueryResponse,
    _IssuesQueryResponse,
    _ItemCommentsFollowUpResponse,
    _PRsQueryResponse,
    _Sync,
    _sync_commits,
    _sync_issues,
    _sync_pull_requests,
    sync,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "graphql_pages"

# (response type, "after" variable) -> fixture file, covering every call a
# full sync makes across all three connections.
_ALL_PAGES: dict[tuple[type, str | None], str] = {
    (_IssuesQueryResponse, None): "issues_page1.json",
    (_IssuesQueryResponse, "issues-cursor-1"): "issues_page2.json",
    (_ItemCommentsFollowUpResponse, "comments-cursor-1"): (
        "issues_page1_comments_page2.json"
    ),
    (_PRsQueryResponse, None): "prs_page1.json",
    (_CommitHistoryQueryResponse, None): "commits_page1.json",
}


def _load(name: str) -> JsonObject:
    """Read one fixture file as a JSON object."""
    return cast("JsonObject", json.loads((_FIXTURES / name).read_text()))


def _stub_graphql(
    pages: dict[tuple[type, str | None], str],
    *,
    fail_at: tuple[type, str | None] | None = None,
) -> Callable[[str, JsonObject, type[msgspec.Struct]], msgspec.Struct]:
    """Build a `client.graphql` stub keyed by (response type, after cursor).

    `fail_at`, if given, raises `RateLimitError` for that one call
    instead of returning a page — simulating exhaustion mid-sync.
    """

    def stub[R: msgspec.Struct](
        _query: str, variables: JsonObject, response: type[R]
    ) -> R:
        key = (response, cast("str | None", variables.get("after")))
        if key == fail_at:
            raise RateLimitError(datetime(2026, 7, 16, 20, 0, tzinfo=UTC))
        return msgspec.convert(_load(pages[key]), response, strict=True)

    return stub


def _client(
    mocker: MockerFixture, pages: dict[tuple[type, str | None], str]
) -> GitHubClient:
    """Build a client whose `graphql` and `pr_diff_text` are fully stubbed."""
    client = GitHubClient("tok")
    mocker.patch.object(client, "graphql", side_effect=_stub_graphql(pages))
    mocker.patch.object(
        client, "pr_diff_text", return_value=("diff --git a/x b/x\n", False)
    )
    return client


def _ctx(
    mocker: MockerFixture,
    fetch: Ledger[FetchEvent],
    pages: dict[tuple[type, str | None], str],
) -> _Sync:
    """Build a `_Sync` context around a fully stubbed client."""
    return _Sync(_client(mocker, pages), fetch, Counter())


def _rebuild(fetch: Ledger[FetchEvent], tmp_path: Path) -> Store:
    """Rebuild a fresh Store from `fetch`, with an empty routing ledger."""
    store = Store.open(tmp_path / "cache.sqlite")
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store


def test_full_sync_appends_expected_nodes(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A full sync appends every node kind the fixtures carry.

    When: `sync` drains a 2-page issues connection (one item with a
      second nested comment page and one null node), a PR page (with a
      review, reactions, files, and a closing issue reference), and a
      commit-history page
    Then: the returned counts match the fixtures' node census
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    client = _client(mocker, _ALL_PAGES)

    result = sync(client, fetch, resume=False)

    assert result.counts == Counter(
        item=3,
        comment=3,
        review=1,
        review_comment=1,
        reaction=2,
        cross_ref=1,
        commit=2,
        pr_diff=1,
        fetch_failed=1,
    )


def test_appended_nodes_satisfy_raw_contract(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Every node a full sync appends parses back through `rebuild`.

    When: the store is rebuilt from a full sync's fetch ledger
    Then: rebuild raises nothing, and the fetched rows are queryable
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    client = _client(mocker, _ALL_PAGES)
    sync(client, fetch, resume=False)

    store = _rebuild(fetch, tmp_path)

    assert store.get_issue_or_pr(IssueOrPRNumber(1)).title.startswith("Softwrap")
    assert len(store.commits()) == 2


def test_nested_comment_pagination(mocker: MockerFixture, tmp_path: Path) -> None:
    """A thread whose comments span two pages yields every comment.

    When: an item's first comments page has `hasNextPage: true`
    Then: sync issues a follow-up query and appends the second page's
      comment too, not just the first
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    _sync_issues(_ctx(mocker, fetch, _ALL_PAGES), resume=False)

    store = _rebuild(fetch, tmp_path)

    comments = store.comments_for(GraphQLId("I_1"))
    assert [c.id for c in comments] == [GraphQLId("C_1"), GraphQLId("C_2")]


def test_injected_relation_ids_attached(mocker: MockerFixture, tmp_path: Path) -> None:
    """Comment/review/review-comment/reaction nodes carry their parent id.

    GraphQL never supplies these; the driver attaches them from the
    parent it drained the connection from.

    When: a full sync appends a comment, a review, a review comment,
      and a reaction
    Then: each one's `data` carries the injected id GraphQL didn't send
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    client = _client(mocker, _ALL_PAGES)
    sync(client, fetch, resume=False)

    by_type: dict[str, list[JsonObject]] = {}
    for event in fetch:
        if event["type"] not in ("cursor", "fetch_failed"):
            by_type.setdefault(event["type"], []).append(
                cast("JsonObject", event["data"])
            )

    comment = next(c for c in by_type["comment"] if c["id"] == "C_3")
    assert comment["itemId"] == "I_3"
    review = by_type["review"][0]
    assert review["prId"] == "I_3"
    review_comment = by_type["review_comment"][0]
    assert review_comment["reviewId"] == review["id"]
    reaction = next(r for r in by_type["reaction"] if r["subjectId"] == "I_1")
    assert reaction["content"] == "THUMBS_UP"
    cross_ref = by_type["cross_ref"][0]
    assert cross_ref == {"srcId": "I_3", "dstId": "I_1", "kind": "closes"}


def test_resume_from_cursor(mocker: MockerFixture, tmp_path: Path) -> None:
    """`--resume` continues a connection from its last persisted cursor.

    When: the issues connection is interrupted by `RateLimitError`
      right after page 1 is fully drained and its cursor persisted,
      then resumed
    Then: the resumed call fetches only page 2 (via `after:
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
        _sync_issues(_Sync(interrupted_client, fetch, Counter()), resume=False)

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

    _sync_issues(_Sync(resumed_client, fetch, Counter()), resume=True)

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

    _sync_issues(_ctx(mocker, fetch, _ALL_PAGES), resume=False)

    failures = [event["data"] for event in fetch if event["type"] == "fetch_failed"]
    assert len(failures) == 1
    assert "null node" in failures[0]["reason"]
    store = _rebuild(fetch, tmp_path)
    assert store.get_issue_or_pr(IssueOrPRNumber(5)).id == "I_2"


def test_pr_diff_appended(mocker: MockerFixture, tmp_path: Path) -> None:
    """A PR's diff combines GraphQL file stats with REST diff text.

    When: `_sync_pull_requests` drains a PR page
    Then: the `pr_diff` event carries per-file stats from GraphQL and
      `diffText`/`truncated` from the REST call
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_pull_requests(_ctx(mocker, fetch, _ALL_PAGES), resume=False)

    diffs = [event["data"] for event in fetch if event["type"] == "pr_diff"]
    assert len(diffs) == 1
    assert diffs[0]["prNumber"] == 10
    assert diffs[0]["files"] == [
        {"path": "internal/display/softwrap.go", "additions": 42, "deletions": 5},
        {"path": "runtime/help/options.md", "additions": 3, "deletions": 0},
    ]
    assert diffs[0]["diffText"] == "diff --git a/x b/x\n"
    assert diffs[0]["truncated"] is False


def test_commit_history_maps_oid_and_author_name(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Commit nodes map `oid` to `sha` and `author.name` to a bare string.

    When: `_sync_commits` drains the default branch's commit history
    Then: each `commit` event's `sha` is the GraphQL `oid`, and
      `author` is the GitActor's bare display name, not `{"login": ...}`
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_commits(_ctx(mocker, fetch, _ALL_PAGES), resume=False)

    commits = [event["data"] for event in fetch if event["type"] == "commit"]
    assert len(commits) == 2
    assert commits[0]["sha"] == "abc123def456"
    assert commits[0]["author"] == "David Allouche"
    assert commits[1]["sha"] == "def456abc789"
    assert commits[1]["author"] == "Octavia Reviewer"
