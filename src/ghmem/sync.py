"""Resumable GraphQL sync: issues, PRs, commits, and discussions.

Fills the fetch ledger that `store.rebuild_from_ledgers` replays.
`sync()` drains the `"issues"`, `"pull_requests"`, `"commits"`, and
`"discussions"` connections in turn, persisting a `cursor` event after
every fully drained page so each connection resumes independently.

Two Struct families exist by design and must not be confused. The
`_Wire` hierarchy here types what GraphQL sends back; `ingest.py`'s
`_Raw*` structs type what gets appended. Every node is encoded from a
wire Struct into a raw `data` dict and passed through
`ingest.validate_raw_node` before it is appended, so a reshaping bug
raises `msgspec.ValidationError` at the offending call instead of
reaching the ledger. A GraphQL connection returning a literal `null`
entry is a real API gap rather than a driver bug, so it is recorded as
`fetch_failed` and skipped — see `_drain_items_page`.

Where this departs from a literal GraphQL schema walk:

- **Cross-references** come from two deliberately separate sources.
  `PullRequest.closingIssuesReferences` supplies `"closes"` edges.
  `timelineItems`, filtered server-side to `CROSS_REFERENCED_EVENT`
  and selected on both issues and PRs, supplies `"cross_referenced"`
  edges — the associative mentions `closingIssuesReferences` never
  carried. Neither source emits the other's edge kind.
- **Reactions** come from `reactionGroups`, GitHub's own non-paginated
  aggregate, rather than from counting individual `reactions`.
- **Timestamps stay `str`** in every wire Struct, passing through to
  the raw `data` dict unmodified, so GitHub's own string can never
  drift through a reformat. Same-shape ISO-8601 strings still compare
  correctly for the `updated_at_hwm` high-water mark.
- **Commit history is a special case.** `Commit.history` takes no
  `orderBy` and GitHub always returns it newest-first, so its
  high-water mark is the first node on a page rather than the last,
  and it resumes by `since:` rather than by cursor — see
  `_sync_commits`.
- **Nested connections other than comments** (reviews, review
  comments, files, closing issues, timelineItems) are taken as a first
  page only, but never silently: each selects `pageInfo`, and a first
  page reporting `hasNextPage` records a `fetch_failed` via
  `_check_truncated` rather than dropping the overflow.
- **Discussion comments and replies are fully drained.** Every
  top-level comment on a discussion is appended before any of that
  discussion's replies, so a reply's parent is always already in the
  ledger. `_append_discussion_comments` owns that ordering, and it is
  what keeps `show.OrphanReplyWarning` from firing on pagination order
  alone.
"""

from collections import Counter
from collections.abc import Callable, Iterator
from string import Template
from typing import NamedTuple, cast

import msgspec

from ghmem.aliases import DiscussionNumber, GraphQLId, IssueOrPRNumber, Json, JsonObject
from ghmem.github import GitHubClient
from ghmem.ingest import validate_raw_node
from ghmem.ledger import CursorData, FetchEvent, Ledger, RawNodeEvent

_OWNER = "micro-editor"
_REPO = "micro"
_PAGE_SIZE = 50
_COMMIT_PAGE_SIZE = 100

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
    author_association: str


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


class _ReviewFields(_Wire):
    """The contract fields of a PullRequestReview node."""

    id: GraphQLId
    author: _Author | None
    state: str
    submitted_at: str
    body: str


class _ReviewNode(_ReviewFields):
    """A wire PullRequestReview node with its first comments page."""

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


class _ItemFields(_Wire):
    """The contract fields of an Issue/PullRequest node.

    `merged_by`/`merged_at` are `None` on an Issue node or an unmerged
    pull request — the only fields here GraphQL may leave out.
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
    author_association: str
    merged_by: _Author | None = None
    merged_at: str | None = None


class _ItemNode(_ItemFields, kw_only=True):
    """A wire Issue/PullRequest node: fields plus first-page nested data.

    `reviews`, `files`, and `closing_issues_references` are `None` on
    an Issue node (GraphQL selects them only inside a `... on
    PullRequest` fragment). `timeline_items` is selected on both —
    cross-references (an issue or PR mentioning another) occur on
    either.
    """

    comments: _CommentsPage
    reaction_groups: list[_ReactionGroupNode]
    timeline_items: _TimelineItemsFirstPage
    reviews: _ReviewsFirstPage | None = None
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


class _DiscussionCommentFields(_Wire):
    """The contract fields of a DiscussionComment, top-level or reply.

    GitHub discussions nest exactly one level (`show.render_discussion`
    makes the same assumption on the read side), so a reply is exactly
    these fields: it selects no `replies` connection of its own, and
    needs no struct distinct from this one.
    """

    id: GraphQLId
    author: _Author | None
    created_at: str
    updated_at: str
    body: str
    author_association: str


type _RepliesFirstPage = _Page[_DiscussionCommentFields]


class _DiscussionCommentNode(_DiscussionCommentFields, kw_only=True):
    """A wire top-level DiscussionComment node with its first replies page."""

    replies: _RepliesFirstPage


type _DiscussionCommentsPage = _Page[_DiscussionCommentNode]


class _DiscussionCategoryNode(_Wire):
    """A wire discussion-category node."""

    name: str


class _DiscussionFields(_Wire):
    """The contract fields of a Discussion node."""

    id: GraphQLId
    number: DiscussionNumber
    title: str
    body: str
    author: _Author | None
    created_at: str
    updated_at: str
    category: _DiscussionCategoryNode
    answer: _NodeRef | None
    author_association: str


class _DiscussionNode(_DiscussionFields, kw_only=True):
    """A wire Discussion node: fields plus its first-page comments."""

    comments: _DiscussionCommentsPage


type _DiscussionsPage = _Page[_DiscussionNode]


class _DiscussionsRepo(_Wire):
    """The `repository.discussions` field of the discussions-page query."""

    discussions: _DiscussionsPage


class _DiscussionsQueryResponse(_Wire):
    """Top-level response for the discussions-page query."""

    repository: _DiscussionsRepo


class _NodeDiscussionComments(_Wire):
    """The `comments` field on a discussion's `node(id:)` follow-up result."""

    comments: _DiscussionCommentsPage


class _DiscussionCommentsFollowUpResponse(_Wire):
    """Top-level response for a discussion's comments follow-up page."""

    node: _NodeDiscussionComments


class _NodeReplies(_Wire):
    """The `replies` field on a comment's `node(id:)` follow-up result."""

    replies: _RepliesFirstPage


class _RepliesFollowUpResponse(_Wire):
    """Top-level response for a comment's replies follow-up page."""

    node: _NodeReplies


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


# --- Issues and pull requests. ---


def _sync_issues(ctx: _Sync) -> None:
    """Drain `repository.issues`, oldest-updated first."""
    after = _resume_cursor(ctx.fetch, "issues")
    while True:
        response = ctx.client.graphql(
            _ISSUES_PAGE_QUERY,
            {"first": _PAGE_SIZE, "after": after},
            response=_IssuesQueryResponse,
        )
        after = _drain_items_page(
            ctx, "issues", response.repository.issues, is_pr=False
        )
        if after is None:
            return


class _Query(Template):
    """A GraphQL query template, with `%name` placeholders.

    GraphQL is brace-dense and spells its own variables `$name`, which
    rules out both of the obvious notations: an f-string would need
    every literal brace in the query doubled, and `string.Template`'s
    default `$` delimiter would collide with `$after`/`$id`/`$since`.
    `%` is not a token in GraphQL's grammar, so a `%name` placeholder
    cannot be mistaken for anything in the query it sits in.

    Values that GraphQL can carry as real query variables are passed
    that way instead — see each query's `graphql(...)` call. What is
    left for this template is what GraphQL has no variables for: the
    repository coordinates and the composed field sets.

    A query built with this reads below the function that issues it, but
    the field-set fragments it composes must read above it: `substitute`
    runs at import, so unlike a name used inside a function body, these
    bind in definition order.
    """

    delimiter = "%"


_COMMENT_FIELDS = "id author { login } createdAt updatedAt body authorAssociation"
"""The `_CommentNode` selection, shared by the item queries and the follow-up.

Selected in three places — an issue's comments, a PR's comments, and
the follow-up page for either — which must stay identical: they all
decode into the same `_CommentNode`.
"""

_ITEM_FIELDS = _Query("""
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
    comments(first: %page_size) {
      pageInfo { hasNextPage endCursor }
      nodes { %comment_fields }
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
""").substitute(page_size=_PAGE_SIZE, comment_fields=_COMMENT_FIELDS)
"""The `_ItemNode` selection common to an issue and a pull request.

Shared by `_ISSUES_PAGE_QUERY` and `_PRS_PAGE_QUERY`, which decode into
the same `_ItemNode`; the PR query appends the `... on PullRequest`
fields on top.
"""

_ISSUES_PAGE_QUERY = _Query("""
query($first: Int!, $after: String) {
  rateLimit { cost remaining resetAt }
  repository(owner: "%owner", name: "%repo") {
    issues(first: $first, after: $after,
           orderBy: {field: UPDATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { %item_fields }
    }
  }
}
""").substitute(owner=_OWNER, repo=_REPO, item_fields=_ITEM_FIELDS)


def _sync_pull_requests(ctx: _Sync) -> None:
    """Drain `repository.pullRequests`, oldest-updated first."""
    after = _resume_cursor(ctx.fetch, "pull_requests")
    while True:
        response = ctx.client.graphql(
            _PRS_PAGE_QUERY,
            {"first": _PAGE_SIZE, "after": after},
            response=_PRsQueryResponse,
        )
        after = _drain_items_page(
            ctx, "pull_requests", response.repository.pull_requests, is_pr=True
        )
        if after is None:
            return


_PRS_PAGE_QUERY = _Query("""
query($first: Int!, $after: String) {
  rateLimit { cost remaining resetAt }
  repository(owner: "%owner", name: "%repo") {
    pullRequests(first: $first, after: $after,
                 orderBy: {field: UPDATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        %item_fields
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
      }
    }
  }
}
""").substitute(owner=_OWNER, repo=_REPO, item_fields=_ITEM_FIELDS)


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
    _append_validated(ctx, {"type": "item", "data": _raw_data(node, _ItemFields)})
    for comment in node.comments.nodes:
        _append_validated(
            ctx,
            {
                "type": "comment",
                "data": _raw_data(comment, _CommentNode, itemId=node.id),
            },
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
            ctx,
            {"type": "review", "data": _raw_data(review, _ReviewFields, prId=node.id)},
        )
        _check_truncated(ctx, review.id, "review comments", review.comments)
        for review_comment in review.comments.nodes:
            _append_validated(
                ctx,
                {
                    "type": "review_comment",
                    "data": _raw_data(
                        review_comment, _ReviewCommentNode, reviewId=review.id
                    ),
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
            {"id": node.id, "first": _PAGE_SIZE, "after": cursor},
            response=_ItemCommentsFollowUpResponse,
        )
        page = response.node.comments
        return page.nodes, page.page_info

    for comment in _drain_follow_up(node.comments.page_info, fetch_page):
        _append_validated(
            ctx,
            {
                "type": "comment",
                "data": _raw_data(comment, _CommentNode, itemId=node.id),
            },
        )


_ITEM_COMMENTS_FOLLOWUP_QUERY = _Query("""
query($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost remaining resetAt }
  node(id: $id) {
    ... on Issue {
      comments(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { %comment_fields }
      }
    }
    ... on PullRequest {
      comments(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { %comment_fields }
      }
    }
  }
}
""").substitute(comment_fields=_COMMENT_FIELDS)


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


# --- Commit history. ---


def _sync_commits(ctx: _Sync) -> None:
    """Drain the default branch's commit history, resuming by `since:`.

    Commits resume differently from every other connection, because
    `Commit.history` takes no `orderBy` and GitHub always returns it
    newest-first. A cursor into a newest-first connection points at
    *older* commits, so resuming from one would drain further into the
    past and never see anything new; catching up means re-entering the
    connection at its head with `since:` set past everything already
    fetched.

    `since:` filters on **committedDate**, not `authoredDate` —
    verified against `micro-editor/micro` on 2026-07-17, where 633 of
    ~3530 commits carry the two dates apart, by up to 2.5 months. The
    high-water mark is therefore the newest `committedDate` seen, and
    `_CommitNode` selects that field for no other reason. `since:` is
    inclusive (also verified), so each catch-up refetches the boundary
    commit; `rebuild_from_ledgers` dedups it on sha.

    The cursor is persisted only once the history is fully drained, so
    a `commits` cursor in the ledger always means "everything up to
    this hwm is present". A run interrupted mid-drain persists nothing
    and is redone from the previous hwm — re-fetching a bounded amount
    rather than stranding the undrained tail behind a hwm that claims
    it was already seen.
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


# --- Discussions. ---


def _sync_discussions(ctx: _Sync) -> None:
    """Drain `repository.discussions`, oldest-updated first."""
    after = _resume_cursor(ctx.fetch, "discussions")
    while True:
        response = ctx.client.graphql(
            _DISCUSSIONS_PAGE_QUERY,
            {"first": _PAGE_SIZE, "after": after},
            response=_DiscussionsQueryResponse,
        )
        after = _drain_discussions_page(ctx, response.repository.discussions)
        if after is None:
            return


_DISCUSSION_COMMENT_FIELDS = """
    id
    author { login }
    createdAt
    updatedAt
    body
    authorAssociation
"""
"""The `_DiscussionCommentFields` selection — a comment or a reply alike."""

_DISCUSSION_COMMENTS_WITH_REPLIES = _Query("""
          %discussion_comment_fields
          replies(first: %page_size) {
            pageInfo { hasNextPage endCursor }
            nodes { %discussion_comment_fields }
          }
""").substitute(
    page_size=_PAGE_SIZE, discussion_comment_fields=_DISCUSSION_COMMENT_FIELDS
)
"""One top-level discussion comment plus its first page of replies.

Selected identically by the discussions page and by the comments
follow-up, because both decode into `_DiscussionCommentNode`.
"""

_DISCUSSIONS_PAGE_QUERY = _Query("""
query($first: Int!, $after: String) {
  rateLimit { cost remaining resetAt }
  repository(owner: "%owner", name: "%repo") {
    discussions(first: $first, after: $after,
                orderBy: {field: UPDATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        number
        title
        body
        author { login }
        createdAt
        updatedAt
        authorAssociation
        category { name }
        answer { id }
        comments(first: %page_size) {
          pageInfo { hasNextPage endCursor }
          nodes { %comments_with_replies }
        }
      }
    }
  }
}
""").substitute(
    owner=_OWNER,
    repo=_REPO,
    page_size=_PAGE_SIZE,
    comments_with_replies=_DISCUSSION_COMMENTS_WITH_REPLIES,
)


def _drain_discussions_page(ctx: _Sync, page: _DiscussionsPage) -> str | None:
    """Append one discussions page, drain comments/replies, persist cursor.

    Every discussion's comments (first page plus any follow-up pages)
    are appended before any of that discussion's replies — see
    `_append_discussion_comments` — so a reply's parent is always
    already in the ledger. Returns the page's `endCursor` to continue
    from, or `None` once `hasNextPage` is false.
    """
    for node in page.nodes:
        _append_validated(
            ctx, {"type": "discussion", "data": _raw_data(node, _DiscussionFields)}
        )
    for node in page.nodes:
        _append_discussion_comments(ctx, node)
    hwm = max((node.updated_at for node in page.nodes), default="")
    if page.page_info.end_cursor is not None:
        ctx.fetch.append(
            {
                "type": "cursor",
                "data": {
                    "connection": "discussions",
                    "top_level_cursor": page.page_info.end_cursor,
                    "updated_at_hwm": hwm,
                },
            }
        )
    return page.page_info.end_cursor if page.page_info.has_next_page else None


def _append_discussion_comments(ctx: _Sync, node: _DiscussionNode) -> None:
    """Append one discussion's comments, then their replies.

    Drains every top-level comment — the embedded first page, plus any
    follow-up pages via `_drain_follow_up` — before draining any
    comment's replies, so a reply's parent comment is always already
    appended by the time the reply is: the orphan-reply property
    (`show.OrphanReplyWarning`) never fires from pagination ordering.
    """
    top_level = list(node.comments.nodes)
    for comment in top_level:
        _append_validated(
            ctx,
            {
                "type": "discussion_comment",
                "data": _raw_data(
                    comment, _DiscussionCommentFields, discussionId=node.id
                ),
            },
        )
    top_level.extend(_drain_discussion_comments_follow_up(ctx, node))
    for comment in top_level:
        _append_comment_replies(ctx, node.id, comment)


def _drain_discussion_comments_follow_up(
    ctx: _Sync, node: _DiscussionNode
) -> list[_DiscussionCommentNode]:
    """Fetch, append, and return one discussion's remaining comment pages."""

    def fetch_page(
        cursor: str,
    ) -> tuple[list[_DiscussionCommentNode], _PageInfo]:
        response = ctx.client.graphql(
            _DISCUSSION_COMMENTS_FOLLOWUP_QUERY,
            {"id": node.id, "first": _PAGE_SIZE, "after": cursor},
            response=_DiscussionCommentsFollowUpResponse,
        )
        page = response.node.comments
        return page.nodes, page.page_info

    drained = list(_drain_follow_up(node.comments.page_info, fetch_page))
    for comment in drained:
        _append_validated(
            ctx,
            {
                "type": "discussion_comment",
                "data": _raw_data(
                    comment, _DiscussionCommentFields, discussionId=node.id
                ),
            },
        )
    return drained


_DISCUSSION_COMMENTS_FOLLOWUP_QUERY = _Query("""
query($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost remaining resetAt }
  node(id: $id) {
    ... on Discussion {
      comments(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { %comments_with_replies }
      }
    }
  }
}
""").substitute(comments_with_replies=_DISCUSSION_COMMENTS_WITH_REPLIES)


def _append_comment_replies(
    ctx: _Sync, discussion_id: GraphQLId, comment: _DiscussionCommentNode
) -> None:
    """Append one comment's first-page replies, drain any follow-up pages."""
    for reply in comment.replies.nodes:
        _append_validated(
            ctx,
            {
                "type": "discussion_comment",
                "data": _raw_data(
                    reply,
                    _DiscussionCommentFields,
                    discussionId=discussion_id,
                    parentId=comment.id,
                ),
            },
        )

    def fetch_page(cursor: str) -> tuple[list[_DiscussionCommentFields], _PageInfo]:
        response = ctx.client.graphql(
            _DISCUSSION_REPLIES_FOLLOWUP_QUERY,
            {"id": comment.id, "first": _PAGE_SIZE, "after": cursor},
            response=_RepliesFollowUpResponse,
        )
        page = response.node.replies
        return page.nodes, page.page_info

    for reply in _drain_follow_up(comment.replies.page_info, fetch_page):
        _append_validated(
            ctx,
            {
                "type": "discussion_comment",
                "data": _raw_data(
                    reply,
                    _DiscussionCommentFields,
                    discussionId=discussion_id,
                    parentId=comment.id,
                ),
            },
        )


_DISCUSSION_REPLIES_FOLLOWUP_QUERY = _Query("""
query($id: ID!, $first: Int!, $after: String) {
  rateLimit { cost remaining resetAt }
  node(id: $id) {
    ... on DiscussionComment {
      replies(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { %discussion_comment_fields }
      }
    }
  }
}
""").substitute(discussion_comment_fields=_DISCUSSION_COMMENT_FIELDS)


# --- Raw-node reshaping: wire Struct -> ingest.py's `data` contract. ---


def _raw_data[F: msgspec.Struct](
    node: _Wire, fields: type[F], **injected: Json
) -> JsonObject:
    """Project a wire node onto `fields` and encode it as raw `data`.

    `fields` is the contract-shaped base of `node`'s wire struct, so
    projecting onto it drops the nested connections the driver drains
    separately and keeps exactly the keys `ingest.py`'s matching
    `_Raw*` struct declares. Encoding rather than hand-copying is what
    keeps the two in step: `rename="camel"` applies on the way out, so
    the wire struct's own field list *is* the emitted key list, and a
    field added on one side cannot silently go missing on the other.

    `injected` carries the relation ids no GraphQL node supplies —
    `itemId`, `prId`, `reviewId`, `discussionId`, `parentId` — which
    the driver knows only from the parent it drained the connection
    from.
    """
    projected = msgspec.convert(node, fields, from_attributes=True)
    return cast("JsonObject", msgspec.to_builtins(projected)) | injected


def _commit_data(commit: _CommitNode) -> JsonObject:
    """Reshape a wire commit node into the `commit` raw-node contract.

    Two transforms with no analogue elsewhere: `oid` becomes `sha`, and
    the `GitActor` node collapses to a bare `author` string — a commit
    author need not be a GitHub account, the one exception to the
    `{"login": ...}` convention. `committedDate` is dropped; it drives
    the `since:` high-water mark only and is not part of the contract.
    """
    return {
        "sha": commit.oid,
        "author": commit.author.name,
        "authoredDate": commit.authored_date,
        "message": commit.message,
    }


# --- Shared plumbing: parse-before-append, follow-up draining, cursors. ---


def _append_validated(ctx: _Sync, event: RawNodeEvent) -> None:
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
    silently losing data, without aborting the sync. `item_id` is the
    connection's owner — the PR for most, but the review for review
    comments. Keyed by `f"{item_id}:{name}"` so two truncated
    connections on one owner stay distinct rows instead of colliding
    on replay.
    """
    if page.page_info.has_next_page:
        count = len(page.nodes)
        reason = (
            f"{name} truncated at first page ({count} nodes, hasNextPage) on {item_id}"
        )
        _append_fetch_failed(ctx, f"{item_id}:{name}", reason)


def _drain_follow_up[T](
    page_info: _PageInfo, fetch_page: Callable[[str], tuple[list[T], _PageInfo]]
) -> Iterator[T]:
    """Yield nodes from follow-up pages after a connection's first page.

    `fetch_page(cursor)` must issue exactly one follow-up GraphQL call
    for `cursor` and return that page's nodes and `pageInfo`. Shared by
    every nested connection that can exceed one page: an item's
    comments, a discussion's comments, and a comment's replies.
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


def _last_hwm(fetch: Ledger[FetchEvent], connection: str) -> str | None:
    """Return `connection`'s last persisted high-water mark, if any."""
    cursor = _last_cursor(fetch, connection)
    return None if cursor is None else cursor["updated_at_hwm"]
