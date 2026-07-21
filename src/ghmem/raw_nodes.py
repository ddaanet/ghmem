"""The raw-node JSON contract: the ten concrete `_Raw*` structs.

Each decodes one `FetchEvent["data"]` object in GraphQL camelCase and
lists its keys in its own docstring. The shared base (`_RawNode`),
its registry (`_RAW_BY_TYPE`), and the small nested-node helper
structs live in `raw_nodes_core.py`, along with the conventions that
apply across every struct here: the `rename="camel"` default, the
`null` author -> `"ghost"` rule, the **injected** field convention,
the optional-key rule (and why `authorAssociation` stays required),
and `forbid_unknown_fields=True`.
"""

from datetime import datetime
from typing import ClassVar

import msgspec

from ghmem.aliases import DiscussionNumber, GraphQLId, IssueOrPRNumber
from ghmem.ledger import RawNodeType
from ghmem.models import (
    Comment,
    Commit,
    CrossRef,
    Discussion,
    DiscussionComment,
    FileStat,
    IssueOrPR,
    PRDiff,
    Reaction,
    Review,
    ReviewComment,
)
from ghmem.raw_nodes_core import (
    _RAW_BY_TYPE,
    _id_of,
    _login_of,
    _RawCategory,
    _RawFileStat,
    _RawIdRef,
    _RawLogin,
    _RawNode,
)

# Re-exported: `ingest.py` imports the registry from here, and it is
# only populated once the concrete structs below have registered
# themselves against the `_RawNode` base imported from `raw_nodes_core`.
__all__ = ["_RAW_BY_TYPE"]


class _RawIssueOrPR(_RawNode):
    """A raw Issue/PullRequest node — the `"item"` event's `data`.

    Keys:

    - `id`
    - `__typename`: `"Issue"` or `"PullRequest"`, the issue/PR
      discriminant
    - `number`, `title`, `state`, `author`
    - `createdAt`, `updatedAt`
    - `body`, `authorAssociation`

    Optional keys:

    - `mergedBy`: `{"login": ...}` or `null`; omitted entirely for a
      non-PR/unmerged node
    - `mergedAt`
    """

    _event_type: ClassVar[RawNodeType] = "item"

    id: GraphQLId
    typename: str = msgspec.field(name="__typename")
    number: IssueOrPRNumber
    title: str
    state: str
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    author_association: str
    merged_by: _RawLogin | None = None
    merged_at: datetime | None = None

    def to_entity(self) -> IssueOrPR:
        return IssueOrPR(
            id=self.id,
            kind="pull_request" if self.typename == "PullRequest" else "issue",
            number=self.number,
            title=self.title,
            state=self.state,
            author=_login_of(self.author),
            created_at=self.created_at,
            updated_at=self.updated_at,
            merged_by=None if self.merged_by is None else _login_of(self.merged_by),
            merged_at=self.merged_at,
            author_association=self.author_association,
            body=self.body,
        )


class _RawComment(_RawNode):
    """A raw IssueComment node — the `"comment"` event's `data`.

    Keys:

    - `id`
    - `itemId`: **injected**, the fetch driver attaches the parent
      issue/PR id
    - `author`
    - `createdAt`, `updatedAt`
    - `body`, `authorAssociation`
    """

    _event_type: ClassVar[RawNodeType] = "comment"

    id: GraphQLId
    item_id: GraphQLId
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    author_association: str

    def to_entity(self) -> Comment:
        return Comment(
            id=self.id,
            item_id=self.item_id,
            author=_login_of(self.author),
            created_at=self.created_at,
            updated_at=self.updated_at,
            author_association=self.author_association,
            body=self.body,
        )


class _RawReview(_RawNode):
    """A raw PullRequestReview node — the `"review"` event's `data`.

    Keys:

    - `id`
    - `prId`: **injected**, like `itemId` on a comment
    - `author`, `state`
    - `submittedAt`
    - `body`
    """

    _event_type: ClassVar[RawNodeType] = "review"

    id: GraphQLId
    pr_id: GraphQLId
    author: _RawLogin | None
    state: str
    submitted_at: datetime
    body: str

    def to_entity(self) -> Review:
        return Review(
            id=self.id,
            pr_id=self.pr_id,
            author=_login_of(self.author),
            state=self.state,
            submitted_at=self.submitted_at,
            body=self.body,
        )


class _RawReviewComment(_RawNode):
    """A raw PullRequestReviewComment node — the `"review_comment"` data.

    Keys:

    - `id`
    - `reviewId`: **injected**
    - `author`
    - `createdAt`, `updatedAt`
    - `body`, `path`
    """

    _event_type: ClassVar[RawNodeType] = "review_comment"

    id: GraphQLId
    review_id: GraphQLId
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    path: str

    def to_entity(self) -> ReviewComment:
        return ReviewComment(
            id=self.id,
            review_id=self.review_id,
            author=_login_of(self.author),
            created_at=self.created_at,
            updated_at=self.updated_at,
            body=self.body,
            path=self.path,
        )


class _RawReaction(_RawNode):
    """A raw aggregated reaction-group node — the `"reaction"` data.

    Keys:

    - `subjectId`: **injected**, the id of the item/comment/review the
      group hangs off
    - `content`, `count`
    """

    _event_type: ClassVar[RawNodeType] = "reaction"

    subject_id: GraphQLId
    content: str
    count: int

    def to_entity(self) -> Reaction:
        return Reaction(
            subject_id=self.subject_id,
            content=self.content,
            count=self.count,
        )


class _RawCrossRef(_RawNode):
    """A cross-reference node — the `"cross_ref"` event's `data`.

    **Wholly synthesized**: no direct GraphQL analogue.

    Keys:

    - `srcId`, `dstId`
    - `kind`: e.g. `"closes"`
    """

    _event_type: ClassVar[RawNodeType] = "cross_ref"

    src_id: GraphQLId
    dst_id: GraphQLId
    kind: str

    def to_entity(self) -> CrossRef:
        return CrossRef(src_id=self.src_id, dst_id=self.dst_id, kind=self.kind)


class _RawCommit(_RawNode):
    """A raw Commit node — the `"commit"` event's `data`.

    `author` is **the one exception** to the `{"login": ...}`
    convention: a bare GitActor display-name string, because a commit
    author need not be a GitHub account.

    Keys:

    - `sha`, `author`
    - `authoredDate`
    - `message`
    """

    _event_type: ClassVar[RawNodeType] = "commit"

    sha: str
    author: str
    authored_date: datetime
    message: str

    def to_entity(self) -> Commit:
        return Commit(
            sha=self.sha,
            author=self.author,
            authored_date=self.authored_date,
            message=self.message,
        )


class _RawPRDiff(_RawNode):
    """A PR-diff node — the `"pr_diff"` event's `data`.

    **Wholly synthesized** from the REST diff endpoint, not a GraphQL
    node.

    Keys:

    - `prNumber`
    - `files`: a list of `_RawFileStat`
    - `diffText`, `truncated`
    """

    _event_type: ClassVar[RawNodeType] = "pr_diff"

    pr_number: IssueOrPRNumber
    files: list[_RawFileStat]
    diff_text: str
    truncated: bool

    def to_entity(self) -> PRDiff:
        return PRDiff(
            pr_number=self.pr_number,
            diffstat=tuple(
                FileStat(path=f.path, additions=f.additions, deletions=f.deletions)
                for f in self.files
            ),
            diff_text=self.diff_text,
            truncated=self.truncated,
        )


class _RawDiscussion(_RawNode):
    """A raw Discussion node — the `"discussion"` event's `data`.

    Keys:

    - `id`, `number`, `title`, `body`, `author`
    - `createdAt`, `updatedAt`
    - `category`: a `{"name": ...}` node
    - `answer`: a `{"id": ...}` node, or `null` if unanswered
    - `authorAssociation`
    """

    _event_type: ClassVar[RawNodeType] = "discussion"

    id: GraphQLId
    number: DiscussionNumber
    title: str
    body: str
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    category: _RawCategory
    answer: _RawIdRef | None
    author_association: str

    def to_entity(self) -> Discussion:
        return Discussion(
            id=self.id,
            number=self.number,
            title=self.title,
            body=self.body,
            author=_login_of(self.author),
            created_at=self.created_at,
            updated_at=self.updated_at,
            category=self.category.name,
            answer_id=_id_of(self.answer),
            author_association=self.author_association,
        )


class _RawDiscussionComment(_RawNode):
    """A raw DiscussionComment node — the `"discussion_comment"` data.

    Keys:

    - `id`
    - `discussionId`: **injected**, the fetch driver attaches the
      parent discussion id
    - `author`
    - `createdAt`, `updatedAt`
    - `body`, `authorAssociation`

    Optional keys:

    - `parentId`: **injected**; omitted/null on a top-level comment,
      the parent comment's id on a reply
    """

    _event_type: ClassVar[RawNodeType] = "discussion_comment"

    id: GraphQLId
    discussion_id: GraphQLId
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    author_association: str
    parent_id: GraphQLId | None = None

    def to_entity(self) -> DiscussionComment:
        return DiscussionComment(
            id=self.id,
            discussion_id=self.discussion_id,
            parent_id=self.parent_id,
            author=_login_of(self.author),
            created_at=self.created_at,
            updated_at=self.updated_at,
            author_association=self.author_association,
            body=self.body,
        )
