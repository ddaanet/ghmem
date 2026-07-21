"""Resumable GraphQL sync for the default branch's commit history.

Commits are a special case among the connections. `Commit.history`
takes no `orderBy`, and GitHub always returns it newest-first. Its
high-water mark is therefore the first node on a page rather than the
last, and it resumes by `since:` rather than by cursor. See
`_sync_commits`.

The wire Structs stay here beside the logic, because the connection is
small enough to keep them local. The plumbing comes from `sync_core`.
"""

from ghmem.aliases import JsonObject
from ghmem.sync_core import (
    _OWNER,
    _REPO,
    _append_validated,
    _last_hwm,
    _Page,
    _Query,
    _Sync,
    _Wire,
)

_COMMIT_PAGE_SIZE = 100


def _sync_commits(ctx: _Sync) -> None:
    """Drain the default branch's commit history, resuming by `since:`.

    Commits resume differently from every other connection, because
    `Commit.history` takes no `orderBy` and GitHub always returns it
    newest-first. A cursor into a newest-first connection points at
    *older* commits, so resuming from one would drain further into the
    past and never see anything new; catching up means re-entering the
    connection at its head with `since:` set past everything already
    fetched.

    `since:` filters on **committedDate**, not `authoredDate`. That was
    verified against `micro-editor/micro` on 2026-07-17, where 633 of
    ~3530 commits carry the two dates apart, by up to 2.5 months. The
    high-water mark is therefore the newest `committedDate` seen, and
    `_CommitNode` selects that field for no other reason.

    `since:` is inclusive (also verified), so each catch-up refetches
    the boundary commit. `rebuild_from_ledgers` dedups it on sha.

    The cursor is persisted only once the history is fully drained, so
    a `commits` cursor in the ledger always means "everything up to
    this hwm is present". A run interrupted mid-drain persists nothing
    and is redone from the previous hwm. That re-fetches a bounded
    amount, rather than stranding the undrained tail behind a hwm that
    claims it was already seen.
    """
    since = _last_hwm(ctx.fetch, "commits")
    hwm = since or ""
    after: str | None = None
    while True:
        response = ctx.client.graphql(
            _COMMIT_HISTORY_QUERY,
            {"first": _COMMIT_PAGE_SIZE, "after": after, "since": since},
            response=_CommitHistoryQueryResponse,
        )
        page = response.repository.default_branch_ref.target.history
        for commit in page.nodes:
            _append_validated(ctx, {"type": "commit", "data": _commit_data(commit)})
        hwm = max([hwm, *(commit.committed_date for commit in page.nodes)])
        if page.page_info.has_next_page:
            after = page.page_info.end_cursor
            continue
        if page.page_info.end_cursor is not None:
            ctx.fetch.append(
                {
                    "type": "cursor",
                    "data": {
                        "connection": "commits",
                        "top_level_cursor": page.page_info.end_cursor,
                        "updated_at_hwm": hwm,
                    },
                }
            )
        return


_COMMIT_HISTORY_QUERY = _Query("""
query($first: Int!, $after: String, $since: GitTimestamp) {
  rateLimit { cost remaining resetAt }
  repository(owner: "%owner", name: "%repo") {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: $first, after: $after, since: $since) {
            pageInfo { hasNextPage endCursor }
            nodes { oid author { name } authoredDate committedDate message }
          }
        }
      }
    }
  }
}
""").substitute(owner=_OWNER, repo=_REPO)


def _commit_data(commit: _CommitNode) -> JsonObject:
    """Reshape a wire commit node into the `commit` raw-node contract.

    Two transforms have no analogue elsewhere:

    - `oid` becomes `sha`.
    - The `GitActor` node collapses to a bare `author` string. A commit
      author need not be a GitHub account, which makes this the one
      exception to the `{"login": ...}` convention.

    `committedDate` is dropped. It drives the `since:` high-water mark
    only, and is not part of the contract.
    """
    return {
        "sha": commit.oid,
        "author": commit.author.name,
        "authoredDate": commit.authored_date,
        "message": commit.message,
    }


class _GitActor(_Wire):
    """A GitActor `{name}` node — a commit author need not be a GitHub user."""

    name: str


class _CommitNode(_Wire):
    """A wire Commit node from a `history` connection.

    `committed_date` is selected but never appended: it exists solely
    to drive the `since:` high-water mark, because that is the date
    `history(since:)` filters on. See `_sync_commits`.
    """

    oid: str
    author: _GitActor
    authored_date: str
    committed_date: str
    message: str


type _CommitHistoryPage = _Page[_CommitNode]


class _CommitHistoryTarget(_Wire):
    """The `Commit`-typed `target` of a `defaultBranchRef`."""

    history: _CommitHistoryPage


class _DefaultBranchRef(_Wire):
    """The `repository.defaultBranchRef` field."""

    target: _CommitHistoryTarget


class _CommitsRepo(_Wire):
    """The `repository` field of the commit-history query."""

    default_branch_ref: _DefaultBranchRef


class _CommitHistoryQueryResponse(_Wire):
    """Top-level response for a commit-history page."""

    repository: _CommitsRepo
