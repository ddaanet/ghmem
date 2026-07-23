"""Append-only JSONL ledger primitive."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Final, Literal, TypedDict, cast

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


type RawNodeType = Literal[
    "item",
    "comment",
    "review",
    "review_comment",
    "reaction",
    "cross_ref",
    "commit",
    "pr_diff",
    "discussion",
    "discussion_comment",
]
"""The `FetchEvent["type"]` tags whose `data` is a raw GraphQL node.

The one enumeration of the raw-node kinds. `ingest.py`'s `_RawNode`
subclasses annotate `_event_type` with this, so a struct claiming a tag
this list doesn't have is a type error. The converse is not checked: a
tag listed here with no struct to parse it stays a `KeyError` raised by
`_RAW_BY_TYPE` at replay, since nothing can see the registry's contents
statically.
"""


class RawNodeEvent(TypedDict):
    """A raw GraphQL node, as fetched, tagged by which kind it is.

    The ledger deliberately does not type `data` beyond `JsonObject`:
    the shape of a raw node is `ingest.py`'s contract (its `_Raw*`
    structs), not this module's, and duplicating it here would be a
    second declaration to keep in step for no added checking.
    """

    type: RawNodeType
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


type FetchEvent = RawNodeEvent | CursorEvent | FetchFailedEvent


NO_SIGNAL: Final[str] = "no-signal"
"""The reserved verdict meaning "reviewed, nothing worth routing".

A real topic name may never equal this — it marks an item as triaged
without creating or dirtying any `Topic` row.
"""


type DispatchFit = Literal["fit", "diff_cut", "oversized"]
"""How a dispatched packet related to the batch budget it was served under.

`fit` — the packet was within budget as rendered. `diff_cut` — it was
a session's first item and its diff body was truncated to bring it
within budget. `oversized` — it was a session's first item and its
thread text and file list alone exceed the budget; the packet was
served with its diff body dropped entirely and only its thread text
and file list intact, neither of those being cuttable.

Subproject 4 calibrates `BATCH_CHARS` against this: a corpus producing
many `oversized` serves has a budget set too low. Like
`RequeueData.session`, it is a write-only signal; the reducer never
reads it back, so it gets no `Dispatch` column. One tag rather than a
pair of booleans, because the three states are mutually exclusive and
a pair would admit a fourth combination that cannot occur.
"""


class DispatchedData(TypedDict):
    """A corpus item served to a triage session.

    `packet_chars` and `fit` place the packet against `BATCH_CHARS`.
    `diff_valve_dropped` places it against the *other* provisional
    constant: the diff-body characters `show_diff.DIFF_BODY_MAX_CHARS`
    removed from this item, and zero when the valve never fired.

    The two are independent axes. A packet can sit comfortably inside
    the batch budget with a 60k diff cut to 25k, which `fit` alone
    reports as an unremarkable `fit`. Both constants are marked as
    calibrated in subproject 4, so both need a signal recorded
    against the items actually served.

    `diff_valve_dropped` is write-only for the same reason `fit` is:
    the reducer never reads it back, so it gets no `Dispatch` column.
    """

    session: str
    item_id: str
    packet_chars: int
    fit: DispatchFit
    diff_valve_dropped: int


class DispatchedEvent(TypedDict):
    """A ghmem-constructed record that an item was dispatched."""

    type: Literal["dispatched"]
    data: DispatchedData


class RouteData(TypedDict):
    """A triage verdict for one item, to a topic or to `NO_SIGNAL`."""

    session: str
    item_id: str
    verdict: str
    note: str


class RouteEvent(TypedDict):
    """A ghmem-constructed record of a triage verdict."""

    type: Literal["route"]
    data: RouteData


class RequeueData(TypedDict):
    """A dispatch stranded by a crashed session, and who abandoned it."""

    session: str
    item_id: str


class RequeueEvent(TypedDict):
    """A ghmem-constructed record that a stranded dispatch was freed.

    One event per stranded item, carrying the session that abandoned it —
    subproject 4 reads that back to calibrate batch budgets: a session that
    strands several items signals its budget was set too high.
    """

    type: Literal["requeue"]
    data: RequeueData


type RoutingEvent = DispatchedEvent | RouteEvent | RequeueEvent
"""The routing-ledger event union.

Grows in Part C (`SnapshotEvent`, `CloseEvent`). Do not add members
outside that part.
"""
