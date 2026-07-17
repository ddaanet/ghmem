"""Shared fixtures and stubs for the split sync test modules.

Not collected by pytest (no `test_` prefix); imported by
`test_sync`, `test_sync_items`, `test_sync_commits`, and
`test_sync_discussions`. `_ALL_PAGES` maps every (response type, after
cursor) a full sync issues to its fixture file, across all four
connections.
"""

import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import msgspec
from pytest_mock import MockerFixture

from ghmem.aliases import JsonObject
from ghmem.exceptions import RateLimitError
from ghmem.github import GitHubClient
from ghmem.ingest import RoutingEvent
from ghmem.ledger import FetchEvent, Ledger
from ghmem.store import Store
from ghmem.sync_commits import _CommitHistoryQueryResponse
from ghmem.sync_core import _Sync
from ghmem.sync_discussions import (
    _DiscussionCommentsFollowUpResponse,
    _DiscussionsQueryResponse,
    _RepliesFollowUpResponse,
)
from ghmem.sync_wire import (
    _IssuesQueryResponse,
    _ItemCommentsFollowUpResponse,
    _PRsQueryResponse,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "graphql_pages"

# (response type, "after" variable) -> fixture file, covering every call a
# full sync makes across all four connections.
_ALL_PAGES: dict[tuple[type, str | None], str] = {
    (_IssuesQueryResponse, None): "issues_page1.json",
    (_IssuesQueryResponse, "issues-cursor-1"): "issues_page2.json",
    (_ItemCommentsFollowUpResponse, "comments-cursor-1"): (
        "issues_page1_comments_page2.json"
    ),
    (_PRsQueryResponse, None): "prs_page1.json",
    (_CommitHistoryQueryResponse, None): "commits_page1.json",
    (_DiscussionsQueryResponse, None): "discussions_page1.json",
    (_DiscussionsQueryResponse, "discussions-cursor-1"): "discussions_page2.json",
    (_DiscussionCommentsFollowUpResponse, "d2-comments-cursor-1"): (
        "discussions_page1_comments_page2.json"
    ),
    (_RepliesFollowUpResponse, "d2-dc1-replies-cursor-1"): (
        "discussions_page1_replies_page2.json"
    ),
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
