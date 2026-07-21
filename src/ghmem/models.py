"""SQLAlchemy declarative entities: schema, identity, and query results.

Each class below is both the SQLite table definition (via `Mapped`
column annotations) and the object `Store` query methods return —
`MappedAsDataclass` gives every entity a generated `__init__` and
value `__eq__`/`__repr__`, so callers compare results the same way
they would a plain dataclass. `ingest.py` owns turning raw GraphQL
JSON into these entities and replaying them into SQLite; this module
only owns what they *are*.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    declared_attr,
    mapped_column,
)
from sqlalchemy.types import TypeDecorator

from ghmem.aliases import DiscussionNumber, GraphQLId, IssueOrPRNumber


@dataclass(frozen=True, slots=True)
class FileStat:
    """Per-file change counts within a `PRDiff`.

    Not its own table — nested JSON inside `PRDiff.diffstat` via
    `_DiffStatType`, so it stays a plain value dataclass rather than a
    declarative entity.
    """

    path: str
    additions: int
    deletions: int


class _ISODateTime(TypeDecorator[datetime]):
    """Store a `datetime` as its `.isoformat()` text, tzinfo included.

    SQLite has no native timezone-aware datetime type, and
    SQLAlchemy's built-in `DateTime` type silently drops tzinfo on the
    sqlite dialect. Every fetched timestamp is UTC (an ISO-8601 "Z"
    string per the raw-node contract below), and round-tripping
    through `isoformat()`/`fromisoformat()` preserves that exactly.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        _dialect: Any,  # noqa: ANN401
    ) -> str | None:
        """Serialize a `datetime` to ISO-8601 text for storage."""
        return None if value is None else value.isoformat()

    def process_result_value(
        self,
        value: str | None,
        _dialect: Any,  # noqa: ANN401
    ) -> datetime | None:
        """Parse stored ISO-8601 text back into a `datetime`."""
        return None if value is None else datetime.fromisoformat(value)


class _DiffStatType(TypeDecorator[tuple[FileStat, ...]]):
    """Store a `tuple[FileStat, ...]` as a JSON array of per-file stats.

    `pr_diffs.diffstat` has no table of its own — it's a small,
    always-loaded-with-the-parent blob, so a JSON column pulls its
    (de)serialization out of `ingest.py` and `store.py` entirely.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(
        self,
        value: tuple[FileStat, ...] | None,
        _dialect: Any,  # noqa: ANN401
    ) -> str | None:
        """Serialize a diffstat tuple to a JSON array of file objects."""
        if value is None:
            return None
        return json.dumps(
            [
                {"path": f.path, "additions": f.additions, "deletions": f.deletions}
                for f in value
            ]
        )

    def process_result_value(
        self,
        value: str | None,
        _dialect: Any,  # noqa: ANN401
    ) -> tuple[FileStat, ...] | None:
        """Parse a stored JSON array back into a diffstat tuple."""
        if value is None:
            return None
        return tuple(FileStat(**f) for f in json.loads(value))


def _camel_to_snake(name: str) -> str:
    """Convert a CamelCase class name to snake_case (`PRDiff` -> `pr_diff`)."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


class Base(MappedAsDataclass, DeclarativeBase, kw_only=True):
    """Declarative base: every entity is also a dataclass (value `__eq__`).

    `kw_only=True` matches how every raw-node parser in `ingest.py`
    already constructs these (all-keyword) and sidesteps dataclass
    field-ordering rules where required and nullable columns
    interleave.
    """

    type_annotation_map: ClassVar[dict[type, Any]] = {
        GraphQLId: String,
        IssueOrPRNumber: Integer,
        DiscussionNumber: Integer,
        datetime: _ISODateTime,
    }

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """Derive each table name from its class, in singular snake_case."""
        return _camel_to_snake(cls.__name__)


class IssueOrPR(Base):
    """An issue or pull request."""

    id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    kind: Mapped[str]
    number: Mapped[IssueOrPRNumber] = mapped_column(unique=True)
    title: Mapped[str]
    state: Mapped[str]
    author: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    merged_by: Mapped[str | None]
    merged_at: Mapped[datetime | None]
    author_association: Mapped[str]
    body: Mapped[str]


class Comment(Base):
    """A general issue/PR comment."""

    id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    item_id: Mapped[GraphQLId]
    author: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    author_association: Mapped[str]
    body: Mapped[str]


class Review(Base):
    """A pull-request review."""

    id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    pr_id: Mapped[GraphQLId]
    author: Mapped[str]
    state: Mapped[str]
    submitted_at: Mapped[datetime]
    body: Mapped[str]


class ReviewComment(Base):
    """A single comment left on a pull-request review."""

    id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    review_id: Mapped[GraphQLId]
    author: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    body: Mapped[str]
    path: Mapped[str]


class Reaction(Base):
    """An aggregated reaction count on some subject."""

    subject_id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(primary_key=True)
    count: Mapped[int]


class CrossRef(Base):
    """A cross-reference from one issue/PR to another (e.g. "closes")."""

    src_id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    dst_id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(primary_key=True)


class Commit(Base):
    """A single commit, as attached to a pull request."""

    sha: Mapped[str] = mapped_column(primary_key=True)
    author: Mapped[str]
    authored_date: Mapped[datetime]
    message: Mapped[str]


class PRDiff(Base):
    """The unified diff and per-file stats for one pull request."""

    pr_number: Mapped[IssueOrPRNumber] = mapped_column(primary_key=True)
    diffstat: Mapped[tuple[FileStat, ...]] = mapped_column(_DiffStatType)
    diff_text: Mapped[str]
    truncated: Mapped[bool]


class Discussion(Base):
    """A GitHub Discussion."""

    id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    number: Mapped[DiscussionNumber] = mapped_column(unique=True)
    title: Mapped[str]
    body: Mapped[str]
    author: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    category: Mapped[str]
    answer_id: Mapped[GraphQLId | None]
    author_association: Mapped[str]


class DiscussionComment(Base):
    """A comment on a discussion."""

    id: Mapped[GraphQLId] = mapped_column(primary_key=True)
    discussion_id: Mapped[GraphQLId]
    parent_id: Mapped[GraphQLId | None]
    author: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    author_association: Mapped[str]
    body: Mapped[str]


class FetchFailed(Base):
    """A single failed fetch, recorded verbatim from the fetch ledger."""

    id: Mapped[str] = mapped_column(primary_key=True)
    reason: Mapped[str]


@dataclass(frozen=True, slots=True)
class HeatScore:
    """One item's heat score, with the per-metric breakdown behind it.

    Not its own table — the result shape of `Store.heat_scores`, whose
    aggregate query computes every field. `title` rides along so
    ranked output can name each item without a second query.
    """

    number: IssueOrPRNumber
    title: str
    score: float
    comments: int
    reactions: int
    participants: int
    related_items: int
    span_years: float
