"""Resumable GraphQL sync entry point: issues, PRs, commits, discussions.

`sync()` fills the fetch ledger that `store.rebuild_from_ledgers`
replays, draining the four connections in turn and delegating each to
its own module — `sync_items` (issues and pull requests), `sync_commits`
(commit history), and `sync_discussions`. The shared machinery those
drivers lean on lives in `sync_core`; the issues/PRs wire Structs, split
out to keep `sync_items` under the file cap, live in `sync_wire`.

Each connection persists a `cursor` event after every fully drained
page, so each resumes independently from its own last checkpoint.
"""

from collections import Counter
from typing import NamedTuple

from ghmem.github import GitHubClient
from ghmem.ledger import CursorData, FetchEvent, Ledger
from ghmem.sync_commits import _sync_commits
from ghmem.sync_core import _last_cursor, _Sync
from ghmem.sync_discussions import _sync_discussions
from ghmem.sync_items import _sync_issues, _sync_pull_requests


class SyncResult(NamedTuple):
    """Outcome of one `sync()` call: per-kind counts and final cursors."""

    counts: Counter[str]
    cursors: dict[str, CursorData]


def sync(client: GitHubClient, fetch: Ledger[FetchEvent]) -> SyncResult:
    """Fetch issues, pull requests, discussions, and commit history.

    Drains the "issues", "pull_requests", "commits", and "discussions"
    connections in turn (issues/PRs bring their reviews, reactions, and
    diffs), appending every node behind parse-before-append and
    persisting a `cursor` event per connection after each page is fully
    drained.

    Every connection always resumes from its own last-persisted
    checkpoint, and one with no checkpoint starts from the beginning.
    Resuming is therefore not a mode: on a first run there is nothing
    to resume from and this drains everything, while on a later run
    re-fetching from the beginning would only re-append nodes that
    `rebuild_from_ledgers` dedups away. Raises `RateLimitError` if the
    budget runs out mid-page; nothing from that in-flight page is
    appended, so the last persisted checkpoint stays the resume point.
    """
    ctx = _Sync(client, fetch, Counter())
    _sync_issues(ctx)
    _sync_pull_requests(ctx)
    _sync_commits(ctx)
    _sync_discussions(ctx)
    cursors = {
        connection: cursor
        for connection in ("issues", "pull_requests", "commits", "discussions")
        if (cursor := _last_cursor(fetch, connection)) is not None
    }
    return SyncResult(ctx.counts, cursors)
