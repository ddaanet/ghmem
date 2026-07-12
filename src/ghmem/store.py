"""SQLite mirror of the ghmem corpus — schema, rebuild, and query API.

SQLite here is a regenerable cache of the append-only ledgers, never the
source of truth: `rebuild_from_ledgers` always drops and recreates the
schema, then replays every ledger event from scratch.
"""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from ghmem.aliases import GraphQLId, Json, JsonObject, Number
from ghmem.exceptions import ItemNotFoundError, PRDiffNotFoundError
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


def _item_from_row(row: sqlite3.Row) -> Item:
    """Reconstruct an `Item` from a `items` table row."""
    return Item(
        id=GraphQLId(row["id"]),
        kind=row["kind"],
        number=Number(row["number"]),
        title=row["title"],
        state=row["state"],
        author=row["author"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        merged_by=row["merged_by"],
        merged_at=(
            datetime.fromisoformat(row["merged_at"]) if row["merged_at"] else None
        ),
        author_association=row["author_association"],
        body=row["body"],
    )


def _comment_from_row(row: sqlite3.Row) -> Comment:
    """Reconstruct a `Comment` from a `comments` table row."""
    return Comment(
        id=GraphQLId(row["id"]),
        item_id=GraphQLId(row["item_id"]),
        author=row["author"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        author_association=row["author_association"],
        body=row["body"],
    )


def _review_from_row(row: sqlite3.Row) -> Review:
    """Reconstruct a `Review` from a `reviews` table row."""
    return Review(
        id=GraphQLId(row["id"]),
        pr_id=GraphQLId(row["pr_id"]),
        author=row["author"],
        state=row["state"],
        submitted_at=datetime.fromisoformat(row["submitted_at"]),
        body=row["body"],
    )


def _review_comment_from_row(row: sqlite3.Row) -> ReviewComment:
    """Reconstruct a `ReviewComment` from a `review_comments` table row."""
    return ReviewComment(
        id=GraphQLId(row["id"]),
        review_id=GraphQLId(row["review_id"]),
        author=row["author"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        body=row["body"],
        path=row["path"],
    )


def _reaction_from_row(row: sqlite3.Row) -> Reaction:
    """Reconstruct a `Reaction` from a `reactions` table row."""
    return Reaction(
        subject_id=GraphQLId(row["subject_id"]),
        content=row["content"],
        count=row["count"],
    )


def _cross_ref_from_row(row: sqlite3.Row) -> CrossRef:
    """Reconstruct a `CrossRef` from a `cross_refs` table row."""
    return CrossRef(
        src_id=GraphQLId(row["src_id"]),
        dst_id=GraphQLId(row["dst_id"]),
        kind=row["kind"],
    )


def _commit_from_row(row: sqlite3.Row) -> Commit:
    """Reconstruct a `Commit` from a `commits` table row."""
    return Commit(
        sha=row["sha"],
        author=row["author"],
        authored_date=datetime.fromisoformat(row["authored_date"]),
        message=row["message"],
    )


def _pr_diff_from_row(row: sqlite3.Row) -> PRDiff:
    """Reconstruct a `PRDiff` from a `pr_diffs` table row."""
    files = json.loads(row["diffstat"])
    return PRDiff(
        pr_number=Number(row["pr_number"]),
        diffstat=tuple(
            FileStat(path=f["path"], additions=f["additions"], deletions=f["deletions"])
            for f in files
        ),
        diff_text=row["diff_text"],
        truncated=bool(row["truncated"]),
    )


class Store:
    """SQLite-backed mirror of the fetched GitHub corpus."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Bind this store to an already-open SQLite connection."""
        self._conn = connection

    @classmethod
    def open(cls, path: Path) -> Store:
        """Open (creating if absent) the SQLite database at `path`."""
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return cls(connection)

    def rebuild_from_ledgers(
        self, fetch: Ledger[FetchEvent], routing: Ledger[RoutingEvent]
    ) -> None:
        """Drop and repopulate every table by replaying both ledgers.

        Replay is idempotent: the whole schema is dropped and recreated
        first, and every row is upserted by id, so rebuilding twice from
        the same ledgers leaves identical query results.

        `routing` is accepted for interface stability with subproject 2
        but not yet replayed — no `RoutingEvent` schema exists. HOOK:
        once routing events are defined, replay them here, after fetch.
        """
        self._conn.executescript(_SCHEMA)
        for event in fetch:
            self._replay_fetch_event(event)
        for _event in routing:
            pass  # no-op stub — see docstring HOOK above.
        self._conn.commit()

    def _replay_fetch_event(self, event: FetchEvent) -> None:
        """Dispatch one `FetchEvent` to its table-specific upsert."""
        if event["type"] == "cursor":
            return  # consumed by Part 3's sync driver, not stored here.
        if event["type"] == "fetch_failed":
            self._upsert_fetch_failed(event["data"])
            return
        self._replay_raw_node_event(event)

    def _replay_raw_node_event(self, event: FetchEvent) -> None:
        """Dispatch one raw-node `FetchEvent` to its table-specific upsert."""
        if event["type"] == "item":
            self._upsert_item(_parse_item(event["data"]))
        elif event["type"] == "comment":
            self._upsert_comment(_parse_comment(event["data"]))
        elif event["type"] == "review":
            self._upsert_review(_parse_review(event["data"]))
        elif event["type"] == "review_comment":
            self._upsert_review_comment(_parse_review_comment(event["data"]))
        elif event["type"] == "reaction":
            self._upsert_reaction(_parse_reaction(event["data"]))
        elif event["type"] == "cross_ref":
            self._upsert_cross_ref(_parse_cross_ref(event["data"]))
        elif event["type"] == "commit":
            self._upsert_commit(_parse_commit(event["data"]))
        elif event["type"] == "pr_diff":
            self._upsert_pr_diff(_parse_pr_diff(event["data"]))

    def _upsert_item(self, item: Item) -> None:
        self._conn.execute(
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

    def _upsert_comment(self, comment: Comment) -> None:
        self._conn.execute(
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

    def _upsert_review(self, review: Review) -> None:
        self._conn.execute(
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

    def _upsert_review_comment(self, comment: ReviewComment) -> None:
        self._conn.execute(
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

    def _upsert_reaction(self, reaction: Reaction) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO reactions VALUES (?, ?, ?)",
            (reaction.subject_id, reaction.content, reaction.count),
        )

    def _upsert_cross_ref(self, cross_ref: CrossRef) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cross_refs VALUES (?, ?, ?)",
            (cross_ref.src_id, cross_ref.dst_id, cross_ref.kind),
        )

    def _upsert_commit(self, commit: Commit) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO commits VALUES (?, ?, ?, ?)",
            (
                commit.sha,
                commit.author,
                commit.authored_date.isoformat(),
                commit.message,
            ),
        )

    def _upsert_pr_diff(self, diff: PRDiff) -> None:
        self._conn.execute(
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

    def _upsert_fetch_failed(self, data: FetchFailedData) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO fetch_failed VALUES (?, ?)",
            (data["id"], data["reason"]),
        )

    def get_item(self, number: Number) -> Item:
        """Return the item numbered `number`.

        Raises `ItemNotFoundError` if no such item was ever fetched.
        """
        row = self._conn.execute(
            "SELECT * FROM items WHERE number = ?", (number,)
        ).fetchone()
        if row is None:
            raise ItemNotFoundError(number)
        return _item_from_row(row)

    def comments_for(self, item_id: GraphQLId) -> list[Comment]:
        """Return the comments on `item_id`, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM comments WHERE item_id = ? ORDER BY created_at",
            (item_id,),
        ).fetchall()
        return [_comment_from_row(row) for row in rows]

    def reviews_for(self, pr_id: GraphQLId) -> list[Review]:
        """Return the reviews on `pr_id`, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM reviews WHERE pr_id = ? ORDER BY submitted_at",
            (pr_id,),
        ).fetchall()
        return [_review_from_row(row) for row in rows]

    def review_comments_for(self, review_id: GraphQLId) -> list[ReviewComment]:
        """Return the comments left on `review_id`, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM review_comments WHERE review_id = ? ORDER BY created_at",
            (review_id,),
        ).fetchall()
        return [_review_comment_from_row(row) for row in rows]

    def reactions_for(self, subject_id: GraphQLId) -> list[Reaction]:
        """Return the reaction-content counts on `subject_id`."""
        rows = self._conn.execute(
            "SELECT * FROM reactions WHERE subject_id = ? ORDER BY content",
            (subject_id,),
        ).fetchall()
        return [_reaction_from_row(row) for row in rows]

    def cross_refs_for(self, item_id: GraphQLId) -> list[CrossRef]:
        """Return cross-references where `item_id` is the source or target."""
        rows = self._conn.execute(
            "SELECT * FROM cross_refs WHERE src_id = ? OR dst_id = ?",
            (item_id, item_id),
        ).fetchall()
        return [_cross_ref_from_row(row) for row in rows]

    def pr_diff_for(self, number: Number) -> PRDiff:
        """Return the diff for the pull request numbered `number`.

        Raises `PRDiffNotFoundError` if no diff was ever fetched for it
        (including when `number` isn't a pull request at all).
        """
        row = self._conn.execute(
            "SELECT * FROM pr_diffs WHERE pr_number = ?", (number,)
        ).fetchone()
        if row is None:
            raise PRDiffNotFoundError(number)
        return _pr_diff_from_row(row)

    def commits(self) -> list[Commit]:
        """Return every fetched commit, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM commits ORDER BY authored_date"
        ).fetchall()
        return [_commit_from_row(row) for row in rows]

    def merges_before(self, d: date) -> list[Item]:
        """Return merged pull requests merged strictly before `d`."""
        rows = self._conn.execute(
            "SELECT * FROM items WHERE merged_at IS NOT NULL"
            " AND date(merged_at) < date(?) ORDER BY merged_at",
            (d.isoformat(),),
        ).fetchall()
        return [_item_from_row(row) for row in rows]

    def items_authored_by(self, user: str) -> list[Item]:
        """Return every item authored by `user`, ordered by number."""
        rows = self._conn.execute(
            "SELECT * FROM items WHERE author = ? ORDER BY number", (user,)
        ).fetchall()
        return [_item_from_row(row) for row in rows]
