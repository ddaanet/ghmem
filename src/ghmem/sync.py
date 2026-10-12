"""Resumable GraphQL sync entry point: issues, PRs, commits, discussions.

`sync()` fills the fetch ledger that `store.rebuild_from_ledgers`
replays. It drains the four connections in turn and delegates each to
its own module: `sync_items` (issues and pull requests),
`sync_commits` (commit history), and `sync_discussions`.

The shared machinery those drivers lean on lives in `sync_core`. The
issues/PRs wire Structs live in `sync_wire`, split out there to keep
`sync_items` under the file cap.

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
from ghmem.target import load_target


class SyncResult(NamedTuple):
    """Outcome of one `sync()` call: per-kind counts and final cursors."""

    counts: Counter[str]
    cursors: dict[str, CursorData]


def sync(client: GitHubClient, fetch: Ledger[FetchEvent]) -> SyncResult:
    """Fetch issues, pull requests, discussions, and commit history.

    Drains the "issues", "pull_requests", "commits", and "discussions"
    connections in turn. Issues and PRs bring their reviews, reactions,
    and diffs with them. Every node is appended behind
    parse-before-append, and a `cursor` event is persisted per
    connection after each page is fully drained.

    Every connection always resumes from its own last-persisted
    checkpoint, and one with no checkpoint starts from the beginning.
    Resuming is therefore not a mode. On a first run there is nothing
    to resume from, so this drains everything. On a later run,
    re-fetching from the beginning would only re-append nodes that
    `rebuild_from_ledgers` dedups away.

    The repository fetched is the corpus's own target, read from its
    `target.toml` before the first request. Raises `TargetConfigError`
    if that file cannot supply it.

    Raises `RateLimitError` if the budget runs out mid-page. Nothing
    from that in-flight page is appended, so the last persisted
    checkpoint stays the resume point.
    """
    ctx = _Sync(client, fetch, Counter(), load_target())
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
