"""Frozen dataclasses returned by `Store` query methods."""

from dataclasses import dataclass
from datetime import datetime

from ghmem.aliases import GraphQLId, Number


@dataclass(frozen=True, slots=True)
class Item:
    """An issue or pull request."""

    id: GraphQLId
    kind: str
    number: Number
    title: str
    state: str
    author: str
    created_at: datetime
    updated_at: datetime
    merged_by: str | None
    merged_at: datetime | None
    author_association: str | None
    body: str


@dataclass(frozen=True, slots=True)
class Comment:
    """A general issue/PR comment."""

    id: GraphQLId
    item_id: GraphQLId
    author: str
    created_at: datetime
    updated_at: datetime
    author_association: str | None
    body: str


@dataclass(frozen=True, slots=True)
class Review:
    """A pull-request review."""

    id: GraphQLId
    pr_id: GraphQLId
    author: str
    state: str
    submitted_at: datetime
    body: str


@dataclass(frozen=True, slots=True)
class ReviewComment:
    """A single comment left on a pull-request review."""

    id: GraphQLId
    review_id: GraphQLId
    author: str
    created_at: datetime
    updated_at: datetime
    body: str
    path: str


@dataclass(frozen=True, slots=True)
class Reaction:
    """An aggregated reaction count on some subject."""

    subject_id: GraphQLId
    content: str
    count: int


@dataclass(frozen=True, slots=True)
class CrossRef:
    """A cross-reference from one item to another (e.g. "closes")."""

    src_id: GraphQLId
    dst_id: GraphQLId
    kind: str


@dataclass(frozen=True, slots=True)
class Commit:
    """A single commit, as attached to a pull request."""

    sha: str
    author: str
    authored_date: datetime
    message: str


@dataclass(frozen=True, slots=True)
class FileStat:
    """Per-file change counts within a `PRDiff`."""

    path: str
    additions: int
    deletions: int


@dataclass(frozen=True, slots=True)
class PRDiff:
    """The unified diff and per-file stats for one pull request."""

    pr_number: Number
    diffstat: tuple[FileStat, ...]
    diff_text: str
    truncated: bool
