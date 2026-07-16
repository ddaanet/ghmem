"""Resumable GraphQL sync: issues, pull requests, and commit history.

Fills the fetch ledger `store.rebuild_from_ledgers` replays. `sync()`
drains three of the repository's connections in turn — `"issues"`,
`"pull_requests"`, `"commits"` — persisting a `cursor` event per
connection after every page is fully drained so `--resume` can
restart each connection independently. Issues and PRs paginate
`UPDATED_AT` ascending; `Commit.history` has no `orderBy` argument and
GitHub always returns it newest-first, so its high-water mark comes
from the first node on a page, not the last. T3 folds `"discussions"`
into the same `sync()` entry point and reuses `_drain_follow_up` below
for comment and reply pagination.

Two Struct families exist by design and must not be confused: the
`_Wire` hierarchy here types what GraphQL sends back (camelCase,
GraphQL connection shapes); `ingest.py`'s `_Raw*` structs type what
gets appended (the ledger's schema-agnostic `data` contract). Every
node this driver appends is reshaped from a wire Struct into a raw
`data` dict and passed through `ingest.validate_raw_node` first —
parse-before-append — so a reshaping bug surfaces immediately as a
raised `msgspec.ValidationError`, not silently, and never reaches the
ledger. That is distinct from a GraphQL connection returning a literal
`null` list entry (an observed real-world API gap, not a driver bug):
that case is recorded as a `fetch_failed` event and skipped, never
raised — see `_drain_items_page`.

Design notes on where this departs from a literal GraphQL schema walk:

- **Cross-references** come from two sources, kept deliberately
  separate rather than merged into one: `PullRequest.
  closingIssuesReferences` (a direct connection of the issues a PR
  closes) still supplies `"closes"` edges, unchanged; `timelineItems`,
  filtered server-side to `CROSS_REFERENCED_EVENT` and selected on
  both issues and PRs, supplies `"cross_referenced"` edges — the
  associative issue↔issue and issue↔PR mentions `closingIssuesReferences`
  never carried. Neither source double-emits the other's edge kind.
- **Reactions** come from `reactionGroups` (GitHub's own aggregate,
  non-paginated `[{content, users{totalCount}}]`), not by paginating
  individual `reactions` and counting client-side.
- **Timestamps stay `str`** end to end in every wire Struct: they pass
  straight through into the raw-node `data` dict unmodified, so there
  is no risk of a reformatting drift from the string GitHub sent.
  ISO-8601 `Z`-suffixed strings of the same shape still compare
  correctly for the `updated_at_hwm` high-water mark.
- **Nested pagination beyond an item's own comments** (reviews, review
  comments, files, closing issues, timelineItems) is taken as a first
  page only, but never silently: each one selects `pageInfo`, and a
  first page reporting `hasNextPage` records a `fetch_failed` instead
  of losing the overflow (`_check_truncated`) — loud truncation, not a
  full drain. `_drain_follow_up` remains ready if a full drain is ever
  needed instead.
"""

from collections import Counter
from collections.abc import Callable, Iterator
from typing import NamedTuple

import msgspec

from ghmem.aliases import GraphQLId, IssueOrPRNumber, Json, JsonObject
from ghmem.github import GitHubClient
from ghmem.ingest import validate_raw_node
from ghmem.ledger import (
    CommentEvent,
    CommitEvent,
    CrossRefEvent,
    CursorData,
    FetchEvent,
    ItemEvent,
    Ledger,
    PRDiffEvent,
    ReactionEvent,
    ReviewCommentEvent,
    ReviewEvent,
)

_OWNER = "micro-editor"
_REPO = "micro"
_PAGE_SIZE = 50
_COMMIT_PAGE_SIZE = 100

type _RawFetchEvent = (
    ItemEvent
    | CommentEvent
    | ReviewEvent
    | ReviewCommentEvent
    | ReactionEvent
    | CrossRefEvent
    | CommitEvent
    | PRDiffEvent
)

# --- Wire Structs: type what GraphQL sends back, not what gets appended. ---


class _Wire(msgspec.Struct, kw_only=True, rename="camel"):
    """Base for GraphQL response wire Structs: kw-only, camelCase keys."""


class _PageInfo(_Wire):
    """A connection's `{hasNextPage, endCursor}` pagination cursor."""

    has_next_page: bool
    end_cursor: str | None


class _Page[T](_Wire):
    """A paginated connection page: `{pageInfo, nodes}`.

    Every first-page-only nested connection (reviews, review comments,
    files, closing issues, timelineItems) selects `pageInfo` too, not
    just `nodes` — `_check_truncated` uses it to detect a first page
    that didn't fully drain the connection.
    """

    page_info: _PageInfo
    nodes: list[T]


class _Author(_Wire):
    """A raw `{login}` author node."""

    login: str


class _NodeRef(_Wire):
    """A bare `{id}` reference to another node."""

    id: GraphQLId


class _CommentNode(_Wire):
    """A wire IssueComment/PullRequestReviewComment-shaped comment."""

    id: GraphQLId
    author: _Author | None
    created_at: str
    updated_at: str
    body: str
    author_association: str | None = None


type _CommentsPage = _Page[_CommentNode]


class _ReviewCommentNode(_Wire):
    """A wire PullRequestReviewComment node."""

    id: GraphQLId
    author: _Author | None
    created_at: str
    updated_at: str
    body: str
    path: str


type _ReviewCommentsFirstPage = _Page[_ReviewCommentNode]


class _ReviewNode(_Wire):
    """A wire PullRequestReview node with its first comments page."""

    id: GraphQLId
    author: _Author | None
    state: str
    submitted_at: str
    body: str
    comments: _ReviewCommentsFirstPage


type _ReviewsFirstPage = _Page[_ReviewNode]


class _UserCount(_Wire):
    """A reacting-users connection's `{totalCount}`."""

    total_count: int


class _ReactionGroupNode(_Wire):
    """One aggregated `{content, users{totalCount}}` reaction group."""

    content: str
    users: _UserCount


class _FileStatNode(_Wire):
    """A `{path, additions, deletions}` diffstat entry."""

    path: str
    additions: int
    deletions: int


type _FilesFirstPage = _Page[_FileStatNode]
type _ClosingIssues = _Page[_NodeRef]


class _ReferencedSubject(_Wire):
    """A `ReferencedSubject` union node (`Issue | PullRequest`), by id.

    Both possible types carry `id`, so the two inline fragments GraphQL
    requires on a union (`... on Issue { id } ... on PullRequest { id
    }`) merge into this one bare `{id}` shape.
    """

    id: GraphQLId


class _CrossReferencedEventNode(_Wire):
    """A wire `CrossReferencedEvent` timelineItems node.

    Only `source` (the referencing issue/PR) is selected — `target`
    (the item whose timeline this event appears on) is always the
    parent `_ItemNode` this connection was queried from, so
    `_append_item_and_nested` injects it as `dstId` the same way it
    injects `itemId` on a comment.
    """

    source: _ReferencedSubject


type _TimelineItemsFirstPage = _Page[_CrossReferencedEventNode]


class _ItemNode(_Wire):
    """A wire Issue/PullRequest node: fields plus first-page nested data.

    `reviews`, `files`, and `closing_issues_references` are `None` on
    an Issue node (GraphQL selects them only inside a `... on
    PullRequest` fragment); `merged_by`/`merged_at` are `None` on an
    Issue node or an unmerged pull request. `timeline_items` is
    selected on both — cross-references (an issue or PR mentioning
    another) occur on either.
    """

    id: GraphQLId
    typename: str = msgspec.field(name="__typename")
    number: IssueOrPRNumber
    title: str
    state: str
    author: _Author | None
    created_at: str
    updated_at: str
    body: str
    comments: _CommentsPage
    reaction_groups: list[_ReactionGroupNode]
    timeline_items: _TimelineItemsFirstPage
    reviews: _ReviewsFirstPage | None = None
    merged_by: _Author | None = None
    merged_at: str | None = None
    author_association: str | None = None
    closing_issues_references: _ClosingIssues | None = None
    files: _FilesFirstPage | None = None


type _ItemsPage = _Page[_ItemNode | None]


class _IssuesRepo(_Wire):
    """The `repository.issues` field of the issues-page query."""

    issues: _ItemsPage


class _IssuesQueryResponse(_Wire):
    """Top-level response for the issues-page query."""

    repository: _IssuesRepo


class _PRsRepo(_Wire):
    """The `repository.pullRequests` field of the PRs-page query."""

    pull_requests: _ItemsPage


class _PRsQueryResponse(_Wire):
    """Top-level response for the pull-requests-page query."""

    repository: _PRsRepo


class _NodeComments(_Wire):
    """The `comments` field on a `node(id:)` follow-up query result."""

    comments: _CommentsPage


class _ItemCommentsFollowUpResponse(_Wire):
    """Top-level response for an item's comments follow-up page."""

    node: _NodeComments


class _GitActor(_Wire):
    """A GitActor `{name}` node — a commit author need not be a GitHub user."""

    name: str


class _CommitNode(_Wire):
    """A wire Commit node from a `history` connection."""

    oid: str
    author: _GitActor
    authored_date: str
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


# --- GraphQL query text. ---

_ITEM_FIELDS = """
    id
    __typename
    number
    title
    state
    author { login }
    createdAt
    updatedAt
    body
    authorAssociation
    comments(first: 50) {
      pageInfo { hasNextPage endCursor }
      nodes { id author { login } createdAt updatedAt body authorAssociation }
    }
    reactionGroups { content users { totalCount } }
    timelineItems(first: 10, itemTypes: [CROSS_REFERENCED_EVENT]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        ... on CrossReferencedEvent {
          source { ... on Issue { id } ... on PullRequest { id } }
        }
      }
    }
"""

_PR_EXTRA_FIELDS = """
    mergedBy { login }
    mergedAt
    reviews(first: 20) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id author { login } state submittedAt body
        comments(first: 20) {
          pageInfo { hasNextPage endCursor }
          nodes { id author { login } createdAt updatedAt body path }
        }
      }
    }
    files(first: 50) {
      pageInfo { hasNextPage endCursor }
      nodes { path additions deletions }
    }
    closingIssuesReferences(first: 10) {
      pageInfo { hasNextPage endCursor }
      nodes { id }
    }
"""

_ISSUES_PAGE_QUERY = f"""
query($after: String) {{
  rateLimit {{ cost remaining resetAt }}
  repository(owner: "{_OWNER}", name: "{_REPO}") {{
    issues(first: {_PAGE_SIZE}, after: $after,
           orderBy: {{field: UPDATED_AT, direction: ASC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ {_ITEM_FIELDS} }}
    }}
  }}
}}
"""

_PRS_PAGE_QUERY = f"""
query($after: String) {{
  rateLimit {{ cost remaining resetAt }}
  repository(owner: "{_OWNER}", name: "{_REPO}") {{
    pullRequests(first: {_PAGE_SIZE}, after: $after,
                 orderBy: {{field: UPDATED_AT, direction: ASC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ {_ITEM_FIELDS} {_PR_EXTRA_FIELDS} }}
    }}
  }}
}}
"""

_ITEM_COMMENTS_FOLLOWUP_QUERY = """
query($id: ID!, $after: String) {
  rateLimit { cost remaining resetAt }
  node(id: $id) {
    ... on Issue {
      comments(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id author { login } createdAt updatedAt body authorAssociation }
      }
    }
    ... on PullRequest {
      comments(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id author { login } createdAt updatedAt body authorAssociation }
      }
    }
  }
}
"""

_COMMIT_HISTORY_QUERY = f"""
query($after: String) {{
  rateLimit {{ cost remaining resetAt }}
  repository(owner: "{_OWNER}", name: "{_REPO}") {{
    defaultBranchRef {{
      target {{
        ... on Commit {{
          history(first: {_COMMIT_PAGE_SIZE}, after: $after) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{ oid author {{ name }} authoredDate message }}
          }}
        }}
      }}
    }}
  }}
}}
"""


# --- sync() and its result. ---


class SyncResult(NamedTuple):
    """Outcome of one `sync()` call: per-kind counts and final cursors."""

    counts: Counter[str]
    cursors: dict[str, CursorData]


class _Sync(NamedTuple):
    """Per-call state threaded through every helper below.

    Bundles the client, the fetch ledger, and the running per-kind counts into
    one value, so no helper needs more than a handful of parameters.
    """

    client: GitHubClient
    fetch: Ledger[FetchEvent]
    counts: Counter[str]


def sync(
    client: GitHubClient, fetch: Ledger[FetchEvent], *, resume: bool
) -> SyncResult:
    """Fetch issues, pull requests (reviews/reactions/diffs), and commits.

    Drains the "issues", "pull_requests", and "commits" connections in
    turn, appending every node behind parse-before-append and
    persisting a `cursor` event per connection after each page is
    fully drained. On `resume=True`, each connection restarts from its
    own last-persisted cursor (`_resume_cursor`); one with no prior
    cursor starts at `after=None` regardless. Raises `RateLimitError`
    if the budget runs out mid-page: nothing from that in-flight page
    is appended, and the ledger's last persisted cursor per connection
    remains the resume point.
    """
    ctx = _Sync(client, fetch, Counter())
    _sync_issues(ctx, resume=resume)
    _sync_pull_requests(ctx, resume=resume)
    _sync_commits(ctx, resume=resume)
    cursors = {
        connection: cursor
        for connection in ("issues", "pull_requests", "commits")
        if (cursor := _last_cursor(fetch, connection)) is not None
    }
    return SyncResult(ctx.counts, cursors)


def _sync_issues(ctx: _Sync, *, resume: bool) -> None:
    """Drain `repository.issues`, oldest-updated first."""
    after = _resume_cursor(ctx.fetch, "issues") if resume else None
    while True:
        response = ctx.client.graphql(
            _ISSUES_PAGE_QUERY, {"after": after}, response=_IssuesQueryResponse
        )
        after = _drain_items_page(
            ctx, "issues", response.repository.issues, is_pr=False
        )
        if after is None:
            return


def _sync_pull_requests(ctx: _Sync, *, resume: bool) -> None:
    """Drain `repository.pullRequests`, oldest-updated first."""
    after = _resume_cursor(ctx.fetch, "pull_requests") if resume else None
    while True:
        response = ctx.client.graphql(
            _PRS_PAGE_QUERY, {"after": after}, response=_PRsQueryResponse
        )
        after = _drain_items_page(
            ctx, "pull_requests", response.repository.pull_requests, is_pr=True
        )
        if after is None:
            return


def _drain_items_page(
    ctx: _Sync, connection: str, page: _ItemsPage, *, is_pr: bool
) -> str | None:
    """Append one items page, drain per-item follow-ups, persist its cursor.

    A `null` list entry (an observed GraphQL gap, not a driver bug) is
    recorded as `fetch_failed` and skipped — it never aborts the page.
    Returns the page's `endCursor` to continue from, or `None` once
    `hasNextPage` is false.
    """
    for index, node in enumerate(page.nodes):
        if node is None:
            _append_fetch_failed(ctx, f"{connection}[{index}]", "null node in page")
    items = [node for node in page.nodes if node is not None]
    for node in items:
        _append_item_and_nested(ctx, node)
    for node in items:
        if node.comments.page_info.has_next_page:
            _drain_item_comments(ctx, node)
    if is_pr:
        for node in items:
            _append_pr_diff(ctx, node)
    hwm = max((node.updated_at for node in items), default="")
    if page.page_info.end_cursor is not None:
        ctx.fetch.append(
            {
                "type": "cursor",
                "data": {
                    "connection": connection,
                    "top_level_cursor": page.page_info.end_cursor,
                    "updated_at_hwm": hwm,
                },
            }
        )
    return page.page_info.end_cursor if page.page_info.has_next_page else None


def _append_item_and_nested(ctx: _Sync, node: _ItemNode) -> None:
    """Append one item plus its first-page comments/reactions/reviews/xrefs."""
    _append_validated(ctx, {"type": "item", "data": _item_data(node)})
    for comment in node.comments.nodes:
        _append_validated(
            ctx, {"type": "comment", "data": _comment_data(comment, node.id)}
        )
    for group in node.reaction_groups:
        if group.users.total_count > 0:
            _append_validated(
                ctx,
                {
                    "type": "reaction",
                    "data": {
                        "subjectId": node.id,
                        "content": group.content,
                        "count": group.users.total_count,
                    },
                },
            )
    _append_reviews(ctx, node)
    _append_cross_refs(ctx, node)
    if node.files is not None:
        _check_truncated(ctx, node.id, "files", node.files)


def _append_reviews(ctx: _Sync, node: _ItemNode) -> None:
    """Append a PR's first-page reviews and their first-page comments."""
    if node.reviews is None:
        return
    _check_truncated(ctx, node.id, "reviews", node.reviews)
    for review in node.reviews.nodes:
        _append_validated(
            ctx, {"type": "review", "data": _review_data(review, node.id)}
        )
        _check_truncated(ctx, node.id, "review comments", review.comments)
        for review_comment in review.comments.nodes:
            _append_validated(
                ctx,
                {
                    "type": "review_comment",
                    "data": _review_comment_data(review_comment, review.id),
                },
            )


def _append_cross_refs(ctx: _Sync, node: _ItemNode) -> None:
    """Append this item's cross_ref edges: closing refs and mentions."""
    if node.closing_issues_references is not None:
        _check_truncated(ctx, node.id, "closing issues", node.closing_issues_references)
        for ref in node.closing_issues_references.nodes:
            _append_validated(
                ctx,
                {
                    "type": "cross_ref",
                    "data": {"srcId": node.id, "dstId": ref.id, "kind": "closes"},
                },
            )
    _check_truncated(ctx, node.id, "timelineItems", node.timeline_items)
    for event in node.timeline_items.nodes:
        _append_validated(
            ctx,
            {
                "type": "cross_ref",
                "data": {
                    "srcId": event.source.id,
                    "dstId": node.id,
                    "kind": "cross_referenced",
                },
            },
        )


def _drain_item_comments(ctx: _Sync, node: _ItemNode) -> None:
    """Fetch and append the remaining pages of one item's comments."""

    def fetch_page(cursor: str) -> tuple[list[_CommentNode], _PageInfo]:
        response = ctx.client.graphql(
            _ITEM_COMMENTS_FOLLOWUP_QUERY,
            {"id": node.id, "after": cursor},
            response=_ItemCommentsFollowUpResponse,
        )
        page = response.node.comments
        return page.nodes, page.page_info

    for comment in _drain_follow_up(node.comments.page_info, fetch_page):
        _append_validated(
            ctx, {"type": "comment", "data": _comment_data(comment, node.id)}
        )


def _append_pr_diff(ctx: _Sync, node: _ItemNode) -> None:
    """Fetch diff text over REST and append the `pr_diff` node."""
    diff_text, truncated = ctx.client.pr_diff_text(node.number)
    files: list[Json] = (
        []
        if node.files is None
        else [
            {"path": f.path, "additions": f.additions, "deletions": f.deletions}
            for f in node.files.nodes
        ]
    )
    data: JsonObject = {
        "prNumber": node.number,
        "files": files,
        "diffText": diff_text,
        "truncated": truncated,
    }
    _append_validated(ctx, {"type": "pr_diff", "data": data})


def _sync_commits(ctx: _Sync, *, resume: bool) -> None:
    """Drain the default branch's commit history, newest-authored first."""
    after = _resume_cursor(ctx.fetch, "commits") if resume else None
    while True:
        response = ctx.client.graphql(
            _COMMIT_HISTORY_QUERY,
            {"after": after},
            response=_CommitHistoryQueryResponse,
        )
        page = response.repository.default_branch_ref.target.history
        for commit in page.nodes:
            _append_validated(
                ctx,
                {
                    "type": "commit",
                    "data": {
                        "sha": commit.oid,
                        "author": commit.author.name,
                        "authoredDate": commit.authored_date,
                        "message": commit.message,
                    },
                },
            )
        hwm = page.nodes[0].authored_date if page.nodes else ""
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
        if not page.page_info.has_next_page:
            return
        after = page.page_info.end_cursor


# --- Raw-node reshaping: wire Struct -> ingest.py's `data` contract. ---


def _author_data(author: _Author | None) -> JsonObject | None:
    """Reshape a wire author node into the raw `{"login": ...}` shape."""
    return None if author is None else {"login": author.login}


def _item_data(node: _ItemNode) -> JsonObject:
    """Reshape a wire item node into the `item` raw-node contract's data."""
    data: JsonObject = {
        "id": node.id,
        "__typename": node.typename,
        "number": node.number,
        "title": node.title,
        "state": node.state,
        "author": _author_data(node.author),
        "createdAt": node.created_at,
        "updatedAt": node.updated_at,
        "body": node.body,
    }
    if node.merged_by is not None:
        data["mergedBy"] = _author_data(node.merged_by)
    if node.merged_at is not None:
        data["mergedAt"] = node.merged_at
    if node.author_association is not None:
        data["authorAssociation"] = node.author_association
    return data


def _comment_data(comment: _CommentNode, item_id: GraphQLId) -> JsonObject:
    """Reshape a wire comment node, injecting its parent `itemId`."""
    data: JsonObject = {
        "id": comment.id,
        "itemId": item_id,
        "author": _author_data(comment.author),
        "createdAt": comment.created_at,
        "updatedAt": comment.updated_at,
        "body": comment.body,
    }
    if comment.author_association is not None:
        data["authorAssociation"] = comment.author_association
    return data


def _review_data(review: _ReviewNode, pr_id: GraphQLId) -> JsonObject:
    """Reshape a wire review node, injecting its parent `prId`."""
    return {
        "id": review.id,
        "prId": pr_id,
        "author": _author_data(review.author),
        "state": review.state,
        "submittedAt": review.submitted_at,
        "body": review.body,
    }


def _review_comment_data(
    review_comment: _ReviewCommentNode, review_id: GraphQLId
) -> JsonObject:
    """Reshape a wire review-comment node, injecting its parent `reviewId`."""
    return {
        "id": review_comment.id,
        "reviewId": review_id,
        "author": _author_data(review_comment.author),
        "createdAt": review_comment.created_at,
        "updatedAt": review_comment.updated_at,
        "body": review_comment.body,
        "path": review_comment.path,
    }


# --- Shared plumbing: parse-before-append, follow-up draining, cursors. ---


def _append_validated(ctx: _Sync, event: _RawFetchEvent) -> None:
    """Validate `event`'s data against the raw-node contract, then append.

    Raises on a contract violation instead of writing: a malformed
    `data` dict is this driver's bug, and must surface immediately as
    a sync error rather than silently corrupt the ledger.
    """
    validate_raw_node(event["type"], event["data"])
    ctx.fetch.append(event)
    ctx.counts[event["type"]] += 1


def _append_fetch_failed(ctx: _Sync, node_id: str, reason: str) -> None:
    """Record a skipped node as `fetch_failed` — never aborts the page."""
    ctx.fetch.append(
        {"type": "fetch_failed", "data": {"id": node_id, "reason": reason}}
    )
    ctx.counts["fetch_failed"] += 1


def _check_truncated[T](
    ctx: _Sync, item_id: GraphQLId, name: str, page: _Page[T]
) -> None:
    """Record a `fetch_failed` if `page`'s first page didn't fully drain.

    Reviews, review comments, files, closing-issue references, and
    timelineItems are all taken as first-page-only by design (never
    drained past `pageInfo`) — this makes an oversized connection's
    truncation visible via `store.fetch_failures()` instead of
    silently losing data, without aborting the sync. Keyed by the
    parent item's id, so a second truncated connection on the same
    item overwrites the first's `fetch_failed` row on replay.
    """
    if page.page_info.has_next_page:
        count = len(page.nodes)
        reason = (
            f"{name} truncated at first page ({count} nodes, hasNextPage) on {item_id}"
        )
        _append_fetch_failed(ctx, item_id, reason)


def _drain_follow_up[T](
    page_info: _PageInfo, fetch_page: Callable[[str], tuple[list[T], _PageInfo]]
) -> Iterator[T]:
    """Yield nodes from follow-up pages after a connection's first page.

    `fetch_page(cursor)` must issue exactly one follow-up GraphQL call
    for `cursor` and return that page's nodes and `pageInfo`. Shared by
    every nested connection that can exceed one page — T3 reuses this
    for discussion comments and replies.
    """
    while page_info.has_next_page:
        assert page_info.end_cursor is not None, "hasNextPage true without endCursor"
        nodes, page_info = fetch_page(page_info.end_cursor)
        yield from nodes


def _last_cursor(fetch: Ledger[FetchEvent], connection: str) -> CursorData | None:
    """Return the most recently persisted cursor for `connection`, if any."""
    last: CursorData | None = None
    for event in fetch:
        if event["type"] == "cursor" and event["data"]["connection"] == connection:
            last = event["data"]
    return last


def _resume_cursor(fetch: Ledger[FetchEvent], connection: str) -> str | None:
    """Return the `after` cursor to resume `connection` from, or `None`."""
    cursor = _last_cursor(fetch, connection)
    return None if cursor is None else cursor["top_level_cursor"]
