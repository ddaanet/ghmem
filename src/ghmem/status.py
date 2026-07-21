"""Coverage report: routing/dispatch counts, topics, and sync cursors.

`build_status` reads the routing tables `ingest.py` replays into (Route,
Dispatch, Topic) plus the fetch ledger's cursor trail — the two ledgers
`heat` and the routing reducer already treat as sources of truth. Only
`ghmem status` calls this; the split exists so the counts and their
rendering are each independently testable.
"""

from dataclasses import dataclass

from ghmem.ledger import NO_SIGNAL, FetchEvent, Ledger
from ghmem.store import Store


@dataclass(frozen=True, slots=True)
class StatusSummary:
    """A snapshot of corpus coverage: counts, topics, dirty set, cursors."""

    total: int
    routed: int
    no_signal: int
    triaged: int
    pending: int
    dispatched: int
    topics: tuple[str, ...]
    dirty: tuple[str, ...]
    cursors: tuple[tuple[str, str], ...]


def build_status(store: Store, fetch: Ledger[FetchEvent]) -> StatusSummary:
    """Assemble a StatusSummary from the SQLite mirror and fetch ledger.

    `pending` is `total - triaged - dispatched`: `dispatched_unrouted`
    already excludes routed items, so the routed-or-no-signal set and
    the stranded-dispatch set are disjoint, and whatever's left over is
    neither.
    """
    total = len(store.all_issues_and_prs())
    routes = store.routes()
    dispatched = len(store.dispatched_unrouted())
    topics = store.topics()

    triaged = len(routes)
    return StatusSummary(
        total=total,
        routed=sum(1 for r in routes if r.verdict != NO_SIGNAL),
        no_signal=sum(1 for r in routes if r.verdict == NO_SIGNAL),
        triaged=triaged,
        pending=total - triaged - dispatched,
        dispatched=dispatched,
        topics=tuple(t.name for t in topics),
        dirty=tuple(t.name for t in topics if t.dirty),
        cursors=tuple(sorted(_last_cursor_per_connection(fetch).items())),
    )


def _last_cursor_per_connection(fetch: Ledger[FetchEvent]) -> dict[str, str]:
    """Scan the fetch ledger for each connection's latest updated_at_hwm."""
    hwm_by_connection: dict[str, str] = {}
    for event in fetch:
        if event["type"] == "cursor":
            data = event["data"]
            hwm_by_connection[data["connection"]] = data["updated_at_hwm"]
    return hwm_by_connection


def render_status(summary: StatusSummary) -> str:
    """Render a compact multi-line coverage report."""
    lines = [
        f"total={summary.total} triaged={summary.triaged} "
        f"routed={summary.routed} no_signal={summary.no_signal} "
        f"pending={summary.pending} dispatched={summary.dispatched}",
        f"topics: {', '.join(summary.topics) or '(none)'}",
        f"dirty: {', '.join(summary.dirty) or '(none)'}",
        *(f"cursor {connection}: {hwm}" for connection, hwm in summary.cursors),
    ]
    return "\n".join(lines)
