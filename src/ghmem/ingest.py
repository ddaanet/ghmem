"""Raw-node ingest: parsing and ledger replay into SQLite.

This is the write half of the store: it owns the raw-GraphQL-node
parsers and `rebuild_from_ledgers`, which drops and replays the whole
mirror from the fetch and routing ledgers. The schema and row identity
live in `models.py`'s declarative entities. The read half (the `Store`
query API) lives in `store.py` and imports `RoutingEvent` and
`rebuild_from_ledgers` from here.

## The raw-node JSON contract

Every `_parse_*` function below decodes one `FetchEvent["data"]`
`JsonObject`. No fetch client exists yet (that's subproject 1 Part 3,
`ghmem sync`) — so this is the contract *it* must produce, not a
description of an existing wire format. Each event `type` requires
exactly these keys, in raw GraphQL camelCase unless noted:

- `"item"` (a raw Issue or PullRequest node): `id`, `__typename`
  (`"Issue"` or `"PullRequest"` — the issue/PR discriminant),
  `number`, `title`, `state`, `author` (a `{"login": ...}` node, or
  `null` for a deleted account — narrowed to the literal string
  `"ghost"`), `createdAt`, `updatedAt`, `mergedBy` (a `{"login": ...}`
  node or `null`; may be omitted entirely for a non-PR/unmerged item),
  `mergedAt` (ISO-8601 or `null`), `authorAssociation` (string or
  `null`), `body`.
- `"comment"` (a raw IssueComment node): `id`, `itemId` — **injected**:
  no such field exists on the real GraphQL node; the fetch driver must
  attach the parent item's id itself — `author` (`{"login": ...}` or
  `null` → `"ghost"`), `createdAt`, `updatedAt`, `authorAssociation`
  (string or `null`), `body`.
- `"review"` (a raw PullRequestReview node): `id`, `prId` —
  **injected**, same as `itemId` above — `author` (`{"login": ...}` or
  `null` → `"ghost"`), `state`, `submittedAt`, `body`.
- `"review_comment"` (a raw PullRequestReviewComment node): `id`,
  `reviewId` — **injected** — `author` (`{"login": ...}` or `null` →
  `"ghost"`), `createdAt`, `updatedAt`, `body`, `path`.
- `"reaction"` (an aggregated reaction-group node): `subjectId` —
  **injected**: the id of the item/comment/review the reaction group
  is attached to — `content`, `count`.
- `"cross_ref"` — **wholly synthesized**, no direct GraphQL analogue:
  `srcId`, `dstId`, `kind` (e.g. `"closes"`).
- `"commit"` (a raw Commit node): `sha`, `author` — **the one
  exception to the `{"login": ...}` convention**: this is a bare
  string (the GitActor's display name), not a login node, because a
  commit author need not be a GitHub account — `authoredDate`,
  `message`.
- `"pr_diff"` — **wholly synthesized** from the REST diff endpoint,
  not a GraphQL node: `prNumber`, `files` (a list of
  `{"path", "additions", "deletions"}` objects), `diffText`,
  `truncated` (bool).

Two event types bypass `_parse_*` entirely: `"cursor"` is consumed by
the sync driver, never stored here; `"fetch_failed"` carries its own
`{"id", "reason"}` shape (`FetchFailedData` in `ledger.py`) straight
into the `fetch_failed` table.
"""

from datetime import datetime

import msgspec
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId, JsonObject, Number
from ghmem.ledger import FetchEvent, Ledger
from ghmem.models import (
    Base,
    Comment,
    Commit,
    CrossRef,
    FetchFailed,
    FileStat,
    Item,
    PRDiff,
    Reaction,
    Review,
    ReviewComment,
)

# Placeholder until subproject 2 defines the real RoutingEvent schema. It
# deliberately doesn't invent routing event kinds — just a JSON object,
# so `rebuild_from_ledgers` has a concrete, mypy-strict-clean signature.
type RoutingEvent = JsonObject


class _RawLogin(msgspec.Struct):
    """A raw `{"login": ...}` GitHub user node."""

    login: str


def _login_of(node: _RawLogin | None) -> str:
    """Resolve a raw author node to a login, mapping a null to "ghost".

    A null author is a deleted/"ghost" GitHub account.
    """
    return "ghost" if node is None else node.login


class _RawItem(msgspec.Struct, rename="camel", kw_only=True):
    """A raw Issue/PullRequest GraphQL node, keyed as fetched."""

    id: str
    typename: str = msgspec.field(name="__typename")
    number: int
    title: str
    state: str
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    merged_by: _RawLogin | None = None
    merged_at: datetime | None = None
    author_association: str | None = None


class _RawComment(msgspec.Struct, rename="camel", kw_only=True):
    """A raw IssueComment GraphQL node, keyed as fetched."""

    id: str
    item_id: str
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    author_association: str | None = None


class _RawReview(msgspec.Struct, rename="camel", kw_only=True):
    """A raw PullRequestReview GraphQL node, keyed as fetched."""

    id: str
    pr_id: str
    author: _RawLogin | None
    state: str
    submitted_at: datetime
    body: str


class _RawReviewComment(msgspec.Struct, rename="camel", kw_only=True):
    """A raw PullRequestReviewComment node, keyed as fetched."""

    id: str
    review_id: str
    author: _RawLogin | None
    created_at: datetime
    updated_at: datetime
    body: str
    path: str


class _RawReaction(msgspec.Struct, rename="camel", kw_only=True):
    """A raw aggregated reaction-group node, keyed as fetched."""

    subject_id: str
    content: str
    count: int


class _RawCrossRef(msgspec.Struct, rename="camel", kw_only=True):
    """A raw cross-reference node, keyed as fetched."""

    src_id: str
    dst_id: str
    kind: str


class _RawCommit(msgspec.Struct, rename="camel", kw_only=True):
    """A raw Commit GraphQL node, keyed as fetched.

    `author` is a bare GitActor display-name string, not a `{"login"}`
    node — a commit author need not be a GitHub account.
    """

    sha: str
    author: str
    authored_date: datetime
    message: str


class _RawFileStat(msgspec.Struct):
    """One `{"path", "additions", "deletions"}` diffstat entry."""

    path: str
    additions: int
    deletions: int


class _RawPRDiff(msgspec.Struct, rename="camel", kw_only=True):
    """A raw PR-diff node, keyed as fetched."""

    pr_number: int
    files: list[_RawFileStat]
    diff_text: str
    truncated: bool


def _parse_item(data: JsonObject) -> Item:
    """Parse a raw Issue/PullRequest GraphQL node into an `Item`."""
    raw = msgspec.convert(data, _RawItem)
    return Item(
        id=GraphQLId(raw.id),
        kind="pull_request" if raw.typename == "PullRequest" else "issue",
        number=Number(raw.number),
        title=raw.title,
        state=raw.state,
        author=_login_of(raw.author),
        created_at=raw.created_at,
        updated_at=raw.updated_at,
        merged_by=None if raw.merged_by is None else _login_of(raw.merged_by),
        merged_at=raw.merged_at,
        author_association=raw.author_association,
        body=raw.body,
    )


def _parse_comment(data: JsonObject) -> Comment:
    """Parse a raw IssueComment GraphQL node into a `Comment`."""
    raw = msgspec.convert(data, _RawComment)
    return Comment(
        id=GraphQLId(raw.id),
        item_id=GraphQLId(raw.item_id),
        author=_login_of(raw.author),
        created_at=raw.created_at,
        updated_at=raw.updated_at,
        author_association=raw.author_association,
        body=raw.body,
    )


def _parse_review(data: JsonObject) -> Review:
    """Parse a raw PullRequestReview GraphQL node into a `Review`."""
    raw = msgspec.convert(data, _RawReview)
    return Review(
        id=GraphQLId(raw.id),
        pr_id=GraphQLId(raw.pr_id),
        author=_login_of(raw.author),
        state=raw.state,
        submitted_at=raw.submitted_at,
        body=raw.body,
    )


def _parse_review_comment(data: JsonObject) -> ReviewComment:
    """Parse a raw PullRequestReviewComment node into a `ReviewComment`."""
    raw = msgspec.convert(data, _RawReviewComment)
    return ReviewComment(
        id=GraphQLId(raw.id),
        review_id=GraphQLId(raw.review_id),
        author=_login_of(raw.author),
        created_at=raw.created_at,
        updated_at=raw.updated_at,
        body=raw.body,
        path=raw.path,
    )


def _parse_reaction(data: JsonObject) -> Reaction:
    """Parse a raw aggregated reaction-group node into a `Reaction`."""
    raw = msgspec.convert(data, _RawReaction)
    return Reaction(
        subject_id=GraphQLId(raw.subject_id),
        content=raw.content,
        count=raw.count,
    )


def _parse_cross_ref(data: JsonObject) -> CrossRef:
    """Parse a raw cross-reference node into a `CrossRef`."""
    raw = msgspec.convert(data, _RawCrossRef)
    return CrossRef(
        src_id=GraphQLId(raw.src_id),
        dst_id=GraphQLId(raw.dst_id),
        kind=raw.kind,
    )


def _parse_commit(data: JsonObject) -> Commit:
    """Parse a raw Commit GraphQL node into a `Commit`."""
    raw = msgspec.convert(data, _RawCommit)
    return Commit(
        sha=raw.sha,
        author=raw.author,
        authored_date=raw.authored_date,
        message=raw.message,
    )


def _parse_pr_diff(data: JsonObject) -> PRDiff:
    """Parse a raw PR-diff node into a `PRDiff`."""
    raw = msgspec.convert(data, _RawPRDiff)
    return PRDiff(
        pr_number=Number(raw.pr_number),
        diffstat=tuple(
            FileStat(path=f.path, additions=f.additions, deletions=f.deletions)
            for f in raw.files
        ),
        diff_text=raw.diff_text,
        truncated=raw.truncated,
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


def _replay_fetch_event(session: Session, event: FetchEvent) -> None:
    """Dispatch one `FetchEvent` to its table-specific upsert."""
    if event["type"] == "cursor":
        return  # consumed by Part 3's sync driver, not stored here.
    if event["type"] == "fetch_failed":
        data = event["data"]
        session.merge(FetchFailed(id=data["id"], reason=data["reason"]))
        return
    _replay_raw_node_event(session, event)


def _replay_raw_node_event(session: Session, event: FetchEvent) -> None:
    """Dispatch one raw-node `FetchEvent` to its table-specific upsert.

    `session.merge` is the upsert: it matches an existing row by
    primary key and updates it in place, or inserts a new one — the
    same "insert or replace by id" semantics the hand-rolled SQL used,
    without a per-table upsert statement to maintain.
    """
    if event["type"] == "item":
        session.merge(_parse_item(event["data"]))
    elif event["type"] == "comment":
        session.merge(_parse_comment(event["data"]))
    elif event["type"] == "review":
        session.merge(_parse_review(event["data"]))
    elif event["type"] == "review_comment":
        session.merge(_parse_review_comment(event["data"]))
    elif event["type"] == "reaction":
        session.merge(_parse_reaction(event["data"]))
    elif event["type"] == "cross_ref":
        session.merge(_parse_cross_ref(event["data"]))
    elif event["type"] == "commit":
        session.merge(_parse_commit(event["data"]))
    elif event["type"] == "pr_diff":
        session.merge(_parse_pr_diff(event["data"]))
