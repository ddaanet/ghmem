"""Resumable GraphQL sync for the issues and pull-requests connections.

`_sync_issues` and `_sync_pull_requests` each drain their connection
oldest-updated first, appending every node behind parse-before-append
and persisting a `cursor` event after every fully drained page. The
wire Structs these decode into live in `sync_wire`; the shared plumbing
they lean on lives in `sync_core`.

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
  drift through a reformat.
- **Nested connections other than comments** (reviews, review
  comments, files, closing issues, timelineItems) are taken as a first
  page only, but never silently: each selects `pageInfo`, and a first
  page reporting `hasNextPage` records a `fetch_failed` via
  `_check_truncated` rather than dropping the overflow.
"""

from ghmem.aliases import Json, JsonObject
from ghmem.sync_core import (
    _OWNER,
    _PAGE_SIZE,
    _REPO,
    _append_fetch_failed,
    _append_validated,
    _check_truncated,
    _drain_follow_up,
    _PageInfo,
    _Query,
    _raw_data,
    _resume_cursor,
    _Sync,
)
from ghmem.sync_wire import (
    _CommentNode,
    _IssuesQueryResponse,
    _ItemCommentsFollowUpResponse,
    _ItemFields,
    _ItemNode,
    _ItemsPage,
    _PRsQueryResponse,
    _ReviewCommentNode,
    _ReviewFields,
)


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
