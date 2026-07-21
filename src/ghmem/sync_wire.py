"""GraphQL wire Structs for the issues and pull-requests connection.

These live apart from the `sync_items` logic that drives them for one
reason only: the item hierarchy plus its drivers and queries exceed the
400-line file cap, and the item structs form a single msgspec cluster
that cannot be cut mid-way. Every other connection (`sync_commits`,
`sync_discussions`) is small enough to keep its structs beside its
logic; issues/PRs is the one that had to be split. The bases these
build on (`_Wire`, `_Page`, `_Author`, `_NodeRef`) stay in `sync_core`.
"""

import msgspec

from ghmem.aliases import GraphQLId, IssueOrPRNumber
from ghmem.sync_core import _Author, _NodeRef, _Page, _ReactionGroupNode, _Wire


class _CommentFields(_Wire):
    """The contract fields of an IssueComment/PullRequestReviewComment."""

    id: GraphQLId
    author: _Author | None
    created_at: str
    updated_at: str
    body: str
    author_association: str


class _CommentNode(_CommentFields, kw_only=True):
    """A wire comment node with its reaction groups."""

    reaction_groups: list[_ReactionGroupNode]


type _CommentsPage = _Page[_CommentNode]


class _ReviewCommentFields(_Wire):
    """The contract fields of a PullRequestReviewComment node."""

    id: GraphQLId
    author: _Author | None
    created_at: str
    updated_at: str
    body: str
    path: str


class _ReviewCommentNode(_ReviewCommentFields, kw_only=True):
    """A wire PullRequestReviewComment node with its reaction groups."""

    reaction_groups: list[_ReactionGroupNode]


type _ReviewCommentsFirstPage = _Page[_ReviewCommentNode]


class _ReviewFields(_Wire):
    """The contract fields of a PullRequestReview node."""

    id: GraphQLId
    author: _Author | None
    state: str
    submitted_at: str
    body: str


class _ReviewNode(_ReviewFields, kw_only=True):
    """A wire PullRequestReview node with its comments page and reactions."""

    comments: _ReviewCommentsFirstPage
    reaction_groups: list[_ReactionGroupNode]


type _ReviewsFirstPage = _Page[_ReviewNode]


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
