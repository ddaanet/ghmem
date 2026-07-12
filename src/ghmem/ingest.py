"""Raw-node ingest: schema, parsing, and ledger replay into SQLite.

This is the write half of the store: it owns the SQLite DDL, the
raw-GraphQL-node parsers, and `rebuild_from_ledgers`, which drops and
replays the whole mirror from the fetch and routing ledgers. The read
half (the `Store` query API) lives in `store.py` and imports
`RoutingEvent` and `rebuild_from_ledgers` from here.

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

import json
import sqlite3
from datetime import datetime

from ghmem.aliases import GraphQLId, Json, JsonObject, Number
from ghmem.ledger import FetchEvent, FetchFailedData, Ledger
from ghmem.models import (
    Comment,
    Commit,
    CrossRef,
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

_SCHEMA = """
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS comments;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS review_comments;
DROP TABLE IF EXISTS reactions;
DROP TABLE IF EXISTS cross_refs;
DROP TABLE IF EXISTS commits;
DROP TABLE IF EXISTS pr_diffs;
DROP TABLE IF EXISTS fetch_failed;

CREATE TABLE items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    number INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    merged_by TEXT,
    merged_at TEXT,
    author_association TEXT,
    body TEXT NOT NULL
);

CREATE TABLE comments (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    author_association TEXT,
    body TEXT NOT NULL
);

CREATE TABLE reviews (
    id TEXT PRIMARY KEY,
    pr_id TEXT NOT NULL,
    author TEXT NOT NULL,
    state TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE TABLE review_comments (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    body TEXT NOT NULL,
    path TEXT NOT NULL
);

CREATE TABLE reactions (
    subject_id TEXT NOT NULL,
    content TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (subject_id, content)
);

CREATE TABLE cross_refs (
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id, kind)
);

CREATE TABLE commits (
    sha TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    authored_date TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE pr_diffs (
    pr_number INTEGER PRIMARY KEY,
    diffstat TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    truncated INTEGER NOT NULL
);

CREATE TABLE fetch_failed (
    id TEXT PRIMARY KEY,
    reason TEXT NOT NULL
);
"""


def _text(value: Json) -> str:
    """Narrow a JSON value known to be a string."""
    assert isinstance(value, str)
    return value


def _number(value: Json) -> int:
    """Narrow a JSON value known to be an int."""
    assert isinstance(value, int)
    return value


def _boolean(value: Json) -> bool:
    """Narrow a JSON value known to be a bool."""
    assert isinstance(value, bool)
    return value


def _optional_text(value: Json) -> str | None:
    """Narrow a JSON value known to be a string or null."""
    if value is None:
        return None
    return _text(value)


def _login(value: Json) -> str:
    """Extract a GitHub user's login from a raw `{"login": ...}` node.

    A null author (a deleted/"ghost" GitHub account) maps to "ghost".
    """
    if value is None:
        return "ghost"
    assert isinstance(value, dict)
    return _text(value.get("login"))


def _timestamp(value: Json) -> datetime:
    """Parse a raw GraphQL ISO-8601 timestamp."""
    return datetime.fromisoformat(_text(value))


def _optional_timestamp(value: Json) -> datetime | None:
    """Parse an optional raw GraphQL ISO-8601 timestamp."""
    if value is None:
        return None
    return _timestamp(value)


def _file_stat(value: Json) -> FileStat:
    """Parse one `{"path", "additions", "deletions"}` diffstat entry."""
    assert isinstance(value, dict)
    return FileStat(
        path=_text(value.get("path")),
        additions=_number(value.get("additions")),
        deletions=_number(value.get("deletions")),
    )


def _parse_item(data: JsonObject) -> Item:
    """Parse a raw Issue/PullRequest GraphQL node into an `Item`."""
    typename = _text(data.get("__typename"))
    merged_by_raw = data.get("mergedBy")
    return Item(
        id=GraphQLId(_text(data.get("id"))),
        kind="pull_request" if typename == "PullRequest" else "issue",
        number=Number(_number(data.get("number"))),
        title=_text(data.get("title")),
        state=_text(data.get("state")),
        author=_login(data.get("author")),
        created_at=_timestamp(data.get("createdAt")),
        updated_at=_timestamp(data.get("updatedAt")),
        merged_by=_login(merged_by_raw) if merged_by_raw is not None else None,
        merged_at=_optional_timestamp(data.get("mergedAt")),
        author_association=_optional_text(data.get("authorAssociation")),
        body=_text(data.get("body")),
    )


def _parse_comment(data: JsonObject) -> Comment:
    """Parse a raw IssueComment GraphQL node into a `Comment`."""
    return Comment(
        id=GraphQLId(_text(data.get("id"))),
        item_id=GraphQLId(_text(data.get("itemId"))),
        author=_login(data.get("author")),
        created_at=_timestamp(data.get("createdAt")),
        updated_at=_timestamp(data.get("updatedAt")),
        author_association=_optional_text(data.get("authorAssociation")),
        body=_text(data.get("body")),
    )


def _parse_review(data: JsonObject) -> Review:
    """Parse a raw PullRequestReview GraphQL node into a `Review`."""
    return Review(
        id=GraphQLId(_text(data.get("id"))),
        pr_id=GraphQLId(_text(data.get("prId"))),
        author=_login(data.get("author")),
        state=_text(data.get("state")),
        submitted_at=_timestamp(data.get("submittedAt")),
        body=_text(data.get("body")),
    )


def _parse_review_comment(data: JsonObject) -> ReviewComment:
    """Parse a raw PullRequestReviewComment node into a `ReviewComment`."""
    return ReviewComment(
        id=GraphQLId(_text(data.get("id"))),
        review_id=GraphQLId(_text(data.get("reviewId"))),
        author=_login(data.get("author")),
        created_at=_timestamp(data.get("createdAt")),
        updated_at=_timestamp(data.get("updatedAt")),
        body=_text(data.get("body")),
        path=_text(data.get("path")),
    )


def _parse_reaction(data: JsonObject) -> Reaction:
    """Parse a raw aggregated reaction-group node into a `Reaction`."""
    return Reaction(
        subject_id=GraphQLId(_text(data.get("subjectId"))),
        content=_text(data.get("content")),
        count=_number(data.get("count")),
    )


def _parse_cross_ref(data: JsonObject) -> CrossRef:
    """Parse a raw cross-reference node into a `CrossRef`."""
    return CrossRef(
        src_id=GraphQLId(_text(data.get("srcId"))),
        dst_id=GraphQLId(_text(data.get("dstId"))),
        kind=_text(data.get("kind")),
    )


def _parse_commit(data: JsonObject) -> Commit:
    """Parse a raw Commit GraphQL node into a `Commit`."""
    return Commit(
        sha=_text(data.get("sha")),
        author=_text(data.get("author")),
        authored_date=_timestamp(data.get("authoredDate")),
        message=_text(data.get("message")),
    )


def _parse_pr_diff(data: JsonObject) -> PRDiff:
    """Parse a raw PR-diff node into a `PRDiff`."""
    files = data.get("files")
    assert isinstance(files, list)
    return PRDiff(
        pr_number=Number(_number(data.get("prNumber"))),
        diffstat=tuple(_file_stat(f) for f in files),
        diff_text=_text(data.get("diffText")),
        truncated=_boolean(data.get("truncated")),
    )


def rebuild_from_ledgers(
    conn: sqlite3.Connection,
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
    conn.executescript(_SCHEMA)
    for event in fetch:
        _replay_fetch_event(conn, event)
    for _event in routing:
        pass  # no-op stub — see docstring HOOK above.
    conn.commit()


def _replay_fetch_event(conn: sqlite3.Connection, event: FetchEvent) -> None:
    """Dispatch one `FetchEvent` to its table-specific upsert."""
    if event["type"] == "cursor":
        return  # consumed by Part 3's sync driver, not stored here.
    if event["type"] == "fetch_failed":
        _upsert_fetch_failed(conn, event["data"])
        return
    _replay_raw_node_event(conn, event)


def _replay_raw_node_event(conn: sqlite3.Connection, event: FetchEvent) -> None:
    """Dispatch one raw-node `FetchEvent` to its table-specific upsert."""
    if event["type"] == "item":
        _upsert_item(conn, _parse_item(event["data"]))
    elif event["type"] == "comment":
        _upsert_comment(conn, _parse_comment(event["data"]))
    elif event["type"] == "review":
        _upsert_review(conn, _parse_review(event["data"]))
    elif event["type"] == "review_comment":
        _upsert_review_comment(conn, _parse_review_comment(event["data"]))
    elif event["type"] == "reaction":
        _upsert_reaction(conn, _parse_reaction(event["data"]))
    elif event["type"] == "cross_ref":
        _upsert_cross_ref(conn, _parse_cross_ref(event["data"]))
    elif event["type"] == "commit":
        _upsert_commit(conn, _parse_commit(event["data"]))
    elif event["type"] == "pr_diff":
        _upsert_pr_diff(conn, _parse_pr_diff(event["data"]))


def _upsert_item(conn: sqlite3.Connection, item: Item) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item.id,
            item.kind,
            item.number,
            item.title,
            item.state,
            item.author,
            item.created_at.isoformat(),
            item.updated_at.isoformat(),
            item.merged_by,
            item.merged_at.isoformat() if item.merged_at else None,
            item.author_association,
            item.body,
        ),
    )


def _upsert_comment(conn: sqlite3.Connection, comment: Comment) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO comments VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            comment.id,
            comment.item_id,
            comment.author,
            comment.created_at.isoformat(),
            comment.updated_at.isoformat(),
            comment.author_association,
            comment.body,
        ),
    )


def _upsert_review(conn: sqlite3.Connection, review: Review) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?, ?)",
        (
            review.id,
            review.pr_id,
            review.author,
            review.state,
            review.submitted_at.isoformat(),
            review.body,
        ),
    )


def _upsert_review_comment(conn: sqlite3.Connection, comment: ReviewComment) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO review_comments VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            comment.id,
            comment.review_id,
            comment.author,
            comment.created_at.isoformat(),
            comment.updated_at.isoformat(),
            comment.body,
            comment.path,
        ),
    )


def _upsert_reaction(conn: sqlite3.Connection, reaction: Reaction) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO reactions VALUES (?, ?, ?)",
        (reaction.subject_id, reaction.content, reaction.count),
    )


def _upsert_cross_ref(conn: sqlite3.Connection, cross_ref: CrossRef) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO cross_refs VALUES (?, ?, ?)",
        (cross_ref.src_id, cross_ref.dst_id, cross_ref.kind),
    )


def _upsert_commit(conn: sqlite3.Connection, commit: Commit) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO commits VALUES (?, ?, ?, ?)",
        (
            commit.sha,
            commit.author,
            commit.authored_date.isoformat(),
            commit.message,
        ),
    )


def _upsert_pr_diff(conn: sqlite3.Connection, diff: PRDiff) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO pr_diffs VALUES (?, ?, ?, ?)",
        (
            diff.pr_number,
            json.dumps(
                [
                    {
                        "path": f.path,
                        "additions": f.additions,
                        "deletions": f.deletions,
                    }
                    for f in diff.diffstat
                ]
            ),
            diff.diff_text,
            diff.truncated,
        ),
    )


def _upsert_fetch_failed(conn: sqlite3.Connection, data: FetchFailedData) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO fetch_failed VALUES (?, ?)",
        (data["id"], data["reason"]),
    )
