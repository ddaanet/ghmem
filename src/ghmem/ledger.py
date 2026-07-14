"""Append-only JSONL ledger primitive."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, TypedDict, cast

from ghmem.aliases import JsonObject


class Ledger[E]:
    """An append-only, newline-delimited JSON event log."""

    def __init__(self, path: Path) -> None:
        """Bind this ledger to `path` without touching the filesystem."""
        self._path = path

    def append(self, event: E) -> None:
        """Append `event` as one JSON line, flushed to the OS on return.

        Closing the handle hands the write to the operating system, so a
        later reader (even a crashed-and-reopened process) sees it. It
        does *not* force the disk: surviving power loss is `sync`'s job,
        called by the caller at its own checkpoints. Bulk ingestion thus
        runs at memory speed and pays for durability only when it wants
        it, instead of an fsync per event baked into every append.
        """
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event))
            handle.write("\n")

    def sync(self) -> None:
        """Force previously appended events to durable storage via fsync.

        A ledger whose file does not exist yet has nothing to force and
        is a no-op, mirroring `__iter__`'s empty-ledger semantics.
        """
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as handle:
            os.fsync(handle.fileno())

    def __iter__(self) -> Iterator[E]:
        """Stream events in append order, skipping a trailing partial line.

        A ledger whose file does not exist yet is empty, not an error:
        `append` creates the file lazily, so "never written" and "no
        events" are the same state. Callers that must tell a missing
        ledger from an empty one can check `exists()` first.
        """
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        # A well-formed file ends with a trailing newline, so the last
        # split element is always "". A crash mid-write instead leaves a
        # non-empty, incomplete final element; skip whichever is last.
        for line in lines[:-1]:
            yield cast("E", json.loads(line))

    def exists(self) -> bool:
        """Return whether the ledger file has been created on disk."""
        return self._path.exists()


class ItemEvent(TypedDict):
    """A raw GraphQL issue/PR node, as fetched."""

    type: Literal["item"]
    data: JsonObject


class CommentEvent(TypedDict):
    """A raw GraphQL comment node, as fetched."""

    type: Literal["comment"]
    data: JsonObject


class ReviewEvent(TypedDict):
    """A raw GraphQL review node, as fetched."""

    type: Literal["review"]
    data: JsonObject


class ReviewCommentEvent(TypedDict):
    """A raw GraphQL review-comment node, as fetched."""

    type: Literal["review_comment"]
    data: JsonObject


class ReactionEvent(TypedDict):
    """A raw GraphQL reaction node, as fetched."""

    type: Literal["reaction"]
    data: JsonObject


class CrossRefEvent(TypedDict):
    """A raw GraphQL cross-reference node, as fetched."""

    type: Literal["cross_ref"]
    data: JsonObject


class CommitEvent(TypedDict):
    """A raw GraphQL commit node, as fetched."""

    type: Literal["commit"]
    data: JsonObject


class PRDiffEvent(TypedDict):
    """A raw GraphQL PR diff node, as fetched."""

    type: Literal["pr_diff"]
    data: JsonObject


class CursorData(TypedDict):
    """Pagination checkpoint recorded by the sync driver."""

    connection: str
    top_level_cursor: str
    updated_at_hwm: str


class CursorEvent(TypedDict):
    """A ghmem-constructed pagination checkpoint."""

    type: Literal["cursor"]
    data: CursorData


class FetchFailedData(TypedDict):
    """Details of a single failed fetch, recorded by the sync driver."""

    id: str
    reason: str


class FetchFailedEvent(TypedDict):
    """A ghmem-constructed record of a failed fetch."""

    type: Literal["fetch_failed"]
    data: FetchFailedData


type FetchEvent = (
    ItemEvent
    | CommentEvent
    | ReviewEvent
    | ReviewCommentEvent
    | ReactionEvent
    | CrossRefEvent
    | CommitEvent
    | PRDiffEvent
    | CursorEvent
    | FetchFailedEvent
)
