"""Raw-node ingest: parsing and ledger replay into SQLite.

This is the write half of the store: it owns the raw-GraphQL-node
parsers and `rebuild_from_ledgers`, which drops and replays the whole
mirror from the fetch and routing ledgers. The schema and row identity
live in `models.py`'s declarative entities; the read half (the `Store`
query API) lives in `store.py`.

## The raw-node JSON contract

No fetch client exists yet — that's subproject 1 Part 3, `ghmem sync`.
So the `_Raw*` structs below are not a description of an existing wire
format: they *are* the contract that client must produce. Each decodes
one `FetchEvent["data"]` `JsonObject`, keyed in raw GraphQL camelCase
(`rename="camel"`) unless a field note says otherwise, and each struct's
docstring lists the keys its event requires. Two conventions recur, so
the per-struct docstrings only flag departures from them:

- a `null` author node narrows to the literal login `"ghost"` (a
  deleted GitHub account);
- a field marked **injected** has no analogue on the real GraphQL node
  and must be attached by the fetch driver itself.

Two event types bypass the raw structs entirely: `"cursor"` is consumed
by the sync driver and never stored here, and `"fetch_failed"` carries
its own `{"id", "reason"}` shape (`FetchFailedData` in `ledger.py`)
straight into the `fetch_failed` table.

The base's `forbid_unknown_fields=True` makes the contract exact: an
undeclared key on a raw-node `data` object is an error, so the sync
driver must select the declared keys from GraphQL and no others.

Replay being this strict, the fetch driver must parse-then-append —
validate a node against its `_RawNode` struct *before* writing it. This
is not a data-loss guard; the ledger is JSONL, so a bad line can be
repaired or dropped by hand. It is about where the error surfaces:
validating at fetch time turns an ingestion error into a sync error,
attributable to the call that produced the bad node, instead of one
`rebuild_from_ledgers` raises later with only a JSON line to work back
from.
"""

from datetime import datetime
from typing import ClassVar

import msgspec
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from ghmem.aliases import DiscussionNumber, GraphQLId, IssueOrPRNumber, JsonObject
from ghmem.ledger import FetchEvent, Ledger
from ghmem.models import (
    Base,
    Comment,
    Commit,
    CrossRef,
    Discussion,
    DiscussionComment,
    FetchFailed,
    FileStat,
    IssueOrPR,
    PRDiff,
    Reaction,
    Review,
    ReviewComment,
)


def rebuild_from_ledgers(
    engine: Engine,
    fetch: Ledger[FetchEvent],
    routing: Ledger[RoutingEvent],
) -> None:
    """Drop and repopulate every table by replaying both ledgers.

    Replay is idempotent: the whole schema is dropped and recreated
    first, and every row is upserted by id, so rebuilding twice from
    the same ledgers leaves identical query results.

    `routing` is accepted for interface stability with subproject 2
    but not yet replayed — no `RoutingEvent` schema exists. HOOK: once
    routing events are defined, replay them here, after fetch.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for event in fetch:
            _replay_fetch_event(session, event)
        for _event in routing:
            pass  # no-op stub — see docstring HOOK above.
        session.commit()


# Placeholder until subproject 2 defines the real RoutingEvent schema. It
# deliberately doesn't invent routing event kinds — just a JSON object,
# so `rebuild_from_ledgers` has a concrete, mypy-strict-clean signature.
type RoutingEvent = JsonObject


def _replay_fetch_event(session: Session, event: FetchEvent) -> None:
    """Dispatch one `FetchEvent` to its table-specific upsert.

    `session.merge` is the upsert: it matches an existing row by primary
    key and updates it in place, or inserts a new one — the same "insert
    or replace by id" semantics the hand-rolled SQL used. `"cursor"` is
    consumed by Part 3's sync driver, not stored; `"fetch_failed"` has
    its own flat shape; every other type is a raw node dispatched by
    `_RAW_BY_TYPE`.
    """
    if event["type"] == "cursor":
        return
    if event["type"] == "fetch_failed":
        data = event["data"]
        session.merge(FetchFailed(id=data["id"], reason=data["reason"]))
        return
    session.merge(_RAW_BY_TYPE[event["type"]].parse(event["data"]))


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

    _event_type: ClassVar[str]

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
    """Resolve a raw author node to a login, mapping a null to "ghost".

    A null author is a deleted/"ghost" GitHub account.
    """
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
    `createdAt`; `updatedAt`; `body`. Optional: `mergedBy`
    (`{"login": ...}` or `null`; omitted entirely for a
    non-PR/unmerged node), `mergedAt`, `authorAssociation`.
    """

    _event_type: ClassVar[str] = "item"

    id: GraphQLId
    typename: str = msgspec.field(name="__typename")
    number: IssueOrPRNumber
    title: str
    state: str
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    merged_by: _RawLogin | None = None
    merged_at: datetime | None = None
    author_association: str | None = None

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
    parent issue/PR id); `author`; `createdAt`; `updatedAt`; `body`.
    Optional: `authorAssociation`.
    """

    _event_type: ClassVar[str] = "comment"

    id: GraphQLId
    item_id: GraphQLId
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    author_association: str | None = None

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

    _event_type: ClassVar[str] = "review"

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

    _event_type: ClassVar[str] = "review_comment"

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

    _event_type: ClassVar[str] = "reaction"

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

    _event_type: ClassVar[str] = "cross_ref"

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

    _event_type: ClassVar[str] = "commit"

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

    _event_type: ClassVar[str] = "pr_diff"

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
    `{"id": ...}` node, or `null` if unanswered). Optional:
    `authorAssociation`.
    """

    _event_type: ClassVar[str] = "discussion"

    id: GraphQLId
    number: DiscussionNumber
    title: str
    body: str
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    category: _RawCategory
    answer: _RawIdRef | None
    author_association: str | None = None

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
    `body`. Optional: `authorAssociation`; `parentId` (**injected**:
    omitted/null on a top-level comment, the parent comment's id on a
    reply).
    """

    _event_type: ClassVar[str] = "discussion_comment"

    id: GraphQLId
    discussion_id: GraphQLId
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    author_association: str | None = None
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
