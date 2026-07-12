"""SQLite mirror of the ghmem corpus — the query API.

SQLite here is a regenerable cache of the append-only ledgers, never
the source of truth: `Store.rebuild_from_ledgers` always drops and
recreates the schema, then replays every ledger event from scratch.
The raw-node parsing, schema DDL, and replay live in `ingest.py`; this
module is the read half — `Store` and its row reconstructors.
"""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from ghmem.aliases import GraphQLId, Number
from ghmem.exceptions import ItemNotFoundError, PRDiffNotFoundError
from ghmem.ingest import RoutingEvent, rebuild_from_ledgers
from ghmem.ledger import FetchEvent, Ledger
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

__all__ = ["RoutingEvent", "Store"]


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
        the same ledgers leaves identical query results. See
        `ingest.rebuild_from_ledgers` for the replay itself and the
        raw-node contract it depends on.
        """
        rebuild_from_ledgers(self._conn, fetch, routing)

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
