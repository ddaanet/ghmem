"""The raw-node JSON contract: one `_Raw*` struct per stored node kind.

These structs *are* the contract `sync.py`'s fetch driver must produce;
`ingest.validate_raw_node` checks every node against them before append.
Each decodes one `FetchEvent["data"]` object in GraphQL camelCase
(`rename="camel"` unless noted) and lists its keys in its own docstring.
Two conventions recur, flagged only on departure: a `null` author
narrows to the login `"ghost"`, and an **injected** field has no GraphQL
analogue and is attached by the driver. A key is optional only where
GitHub can omit it (`mergedBy`/`mergedAt`); `authorAssociation` stays
required because GraphQL types it non-null, so dropping it fails loudly
instead of emptying the column. `forbid_unknown_fields=True` makes the
contract exact.
"""

from datetime import datetime
from typing import ClassVar

import msgspec

from ghmem.aliases import DiscussionNumber, GraphQLId, IssueOrPRNumber, JsonObject
from ghmem.ledger import RawNodeType
from ghmem.models import (
    Base,
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

# Populated by `_RawNode.__init_subclass__`: each raw node registers
# itself here under its `FetchEvent["type"]`, so replay dispatches by
# type without a central lookup to keep in sync with the class list.
_RAW_BY_TYPE: dict[str, type[_RawNode]] = {}


class _RawNode(
    msgspec.Struct, rename="camel", kw_only=True, forbid_unknown_fields=True
):
    """Base for raw-node structs that each map to one declarative entity.

    A subclass sets `_event_type` (its `FetchEvent["type"]`) and
    implements `to_entity`; `parse` decodes the raw JSON once, here, and
    hands the typed struct to `to_entity`. Registration and the shared
    `msgspec.convert` live on the base so a new node kind is a single
    self-contained class.
    """

    _event_type: ClassVar[RawNodeType]

    def __init_subclass__(cls) -> None:
        # msgspec's metaclass consumes every struct-config class kwarg
        # (`rename`, `kw_only`, ...) before this runs, so no kwargs reach
        # here to forward.
        super().__init_subclass__()
        event_type = cls.__dict__.get("_event_type")
        if event_type is not None:
            _RAW_BY_TYPE[event_type] = cls

    @classmethod
    def parse(cls, data: JsonObject) -> Base:
        """Decode one raw `data` object into its declarative entity."""
        return msgspec.convert(data, cls).to_entity()

    def to_entity(self) -> Base:
        """Build the declarative entity from this decoded raw node."""
        raise NotImplementedError


class _RawLogin(msgspec.Struct):
    """A raw `{"login": ...}` GitHub user node."""

    login: str


def _login_of(node: _RawLogin | None) -> str:
    """Resolve a raw author node to a login, mapping a null to "ghost"."""
    return "ghost" if node is None else node.login


class _RawFileStat(msgspec.Struct):
    """One `{"path", "additions", "deletions"}` diffstat entry."""

    path: str
    additions: int
    deletions: int


class _RawCategory(msgspec.Struct):
    """A raw `{"name": ...}` discussion-category node."""

    name: str


class _RawIdRef(msgspec.Struct):
    """A raw `{"id": ...}` reference to another node, by id."""

    id: GraphQLId


def _id_of(ref: _RawIdRef | None) -> GraphQLId | None:
    """Resolve a raw id-reference node to its id, mapping null to None."""
    return None if ref is None else ref.id


class _RawIssueOrPR(_RawNode):
    """A raw Issue/PullRequest node — the `"item"` event's `data`.

    Keys: `id`; `__typename` (`"Issue"` or `"PullRequest"`, the
    issue/PR discriminant); `number`; `title`; `state`; `author`;
    `createdAt`; `updatedAt`; `body`; `authorAssociation`. Optional:
    `mergedBy` (`{"login": ...}` or `null`; omitted entirely for a
    non-PR/unmerged node), `mergedAt`.
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

    Keys: `id`; `itemId` (**injected**: the fetch driver attaches the
    parent issue/PR id); `author`; `createdAt`; `updatedAt`; `body`;
    `authorAssociation`.
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

    Keys: `id`; `prId` (**injected**, like `itemId` on a comment);
    `author`; `state`; `submittedAt`; `body`.
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

    Keys: `id`; `reviewId` (**injected**); `author`; `createdAt`;
    `updatedAt`; `body`; `path`.
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

    Keys: `subjectId` (**injected**: the id of the item/comment/review
    the group hangs off), `content`, `count`.
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

    **Wholly synthesized**: no direct GraphQL analogue. Keys: `srcId`,
    `dstId`, `kind` (e.g. `"closes"`).
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
    author need not be a GitHub account. Keys: `sha`, `author`,
    `authoredDate`, `message`.
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
    node. Keys: `prNumber`; `files` (a list of `_RawFileStat`);
    `diffText`; `truncated`.
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

    Keys: `id`; `number`; `title`; `body`; `author`; `createdAt`;
    `updatedAt`; `category` (a `{"name": ...}` node); `answer` (a
    `{"id": ...}` node, or `null` if unanswered); `authorAssociation`.
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

    Keys: `id`; `discussionId` (**injected**: the fetch driver attaches
    the parent discussion id); `author`; `createdAt`; `updatedAt`;
    `body`; `authorAssociation`. Optional: `parentId` (**injected**:
    omitted/null on a top-level comment, the parent comment's id on a
    reply).
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
