"""Resumable GraphQL sync for the discussions connection.

`_sync_discussions` drains `repository.discussions` oldest-updated
first, appending every node behind parse-before-append and persisting a
`cursor` event per fully drained page.

Every top-level comment on a discussion is appended before any of that
discussion's replies, so a reply's parent is always already in the
ledger — `_append_discussion_comments` owns that ordering, and it is
what keeps `show.OrphanReplyWarning` from firing on pagination order
alone.

The wire Structs stay here beside the logic; the plumbing comes from
`sync_core`.
"""

from ghmem.aliases import DiscussionNumber, GraphQLId
from ghmem.sync_core import (
    _PAGE_SIZE,
    _REACTION_GROUPS,
    _append_fetch_failed,
    _append_reactions,
    _append_validated,
    _Author,
    _drain_follow_up,
    _NodeRef,
    _Page,
    _PageInfo,
    _paginate,
    _persist_cursor,
    _Query,
    _raw_data,
    _ReactionGroupNode,
    _Sync,
    _Wire,
)
from ghmem.target import Target


def _sync_discussions(ctx: _Sync) -> None:
    """Drain `repository.discussions`, oldest-updated first."""
    pages = _paginate(
        ctx,
        "discussions",
        _discussions_page_query(ctx.target),
        _DiscussionsQueryResponse,
        lambda response: response.repository.discussions,
    )
    for page in pages:
        _drain_discussions_page(ctx, page)


_DISCUSSION_COMMENT_FIELDS = _Query("""
    id
    author { login }
    createdAt
    updatedAt
    body
    authorAssociation
    %reaction_groups
""").substitute(reaction_groups=_REACTION_GROUPS)
"""The `_DiscussionCommentWithReactions` selection — a comment or a reply
alike."""

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


def _discussions_page_query(target: Target) -> str:
    """Build the discussions page query against `target`'s repository."""
    return _DISCUSSIONS_PAGE_TEMPLATE.substitute(
        owner=target.owner,
        repo=target.repo,
        page_size=_PAGE_SIZE,
        reaction_groups=_REACTION_GROUPS,
        comments_with_replies=_DISCUSSION_COMMENTS_WITH_REPLIES,
    )


_DISCUSSIONS_PAGE_TEMPLATE = _Query("""
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
        %reaction_groups
        comments(first: %page_size) {
          pageInfo { hasNextPage endCursor }
          nodes { %comments_with_replies }
        }
      }
    }
  }
}
""")


def _drain_discussions_page(ctx: _Sync, page: _DiscussionsPage) -> None:
    """Append one discussions page, drain comments/replies, persist cursor.

    Every discussion's comments (first page plus any follow-up pages)
    are appended before any of that discussion's replies — see
    `_append_discussion_comments` — so a reply's parent is always
    already in the ledger.

    A `null` list entry is recorded as `fetch_failed` and skipped,
    exactly as `_drain_items_page` handles the same observed GraphQL
    gap. Letting the `None` through instead would raise mid-page,
    before this page's cursor was persisted — so the next run would
    re-fetch the same page, hit the same null, and abort again. The
    connection would never advance past a single bad node.
    """
    for index, node in enumerate(page.nodes):
        if node is None:
            _append_fetch_failed(ctx, f"discussions[{index}]", "null node in page")
    discussions = [node for node in page.nodes if node is not None]
    for node in discussions:
        _append_validated(
            ctx, {"type": "discussion", "data": _raw_data(node, _DiscussionFields)}
        )
        _append_reactions(ctx, node.id, node.reaction_groups)
    for node in discussions:
        _append_discussion_comments(ctx, node)
    hwm = max((node.updated_at for node in discussions), default="")
    _persist_cursor(ctx, "discussions", page, hwm)


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
        _append_reactions(ctx, comment.id, comment.reaction_groups)
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
        _append_reactions(ctx, comment.id, comment.reaction_groups)
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
        _append_reactions(ctx, reply.id, reply.reaction_groups)

    def fetch_page(
        cursor: str,
    ) -> tuple[list[_DiscussionCommentWithReactions], _PageInfo]:
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
        _append_reactions(ctx, reply.id, reply.reaction_groups)


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


class _DiscussionCommentFields(_Wire):
    """The contract fields of a DiscussionComment, top-level or reply."""

    id: GraphQLId
    author: _Author | None
    created_at: str
    updated_at: str
    body: str
    author_association: str


class _DiscussionCommentWithReactions(_DiscussionCommentFields, kw_only=True):
    """A wire DiscussionComment/reply node with its reaction groups.

    GitHub discussions nest exactly one level (`show.render_discussion`
    makes the same assumption on the read side), so a reply is exactly
    these fields plus reactions: it selects no `replies` connection of
    its own, and needs no struct distinct from this one.
    """

    reaction_groups: list[_ReactionGroupNode]


type _RepliesFirstPage = _Page[_DiscussionCommentWithReactions]


class _DiscussionCommentNode(_DiscussionCommentWithReactions, kw_only=True):
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
    """A wire Discussion node: fields, first-page comments, reactions."""

    comments: _DiscussionCommentsPage
    reaction_groups: list[_ReactionGroupNode]


type _DiscussionsPage = _Page[_DiscussionNode | None]
"""A discussions page, whose entries GitHub may return as `null`.

Same observed gap as the items connections — see `_ItemsPage` — and
handled the same way by `_drain_discussions_page`.
"""


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
