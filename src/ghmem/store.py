"""SQLAlchemy mirror of the ghmem corpus — the query API.

SQLite here is a regenerable cache of the append-only ledgers, never
the source of truth: `Store.rebuild_from_ledgers` always drops and
recreates the schema, then replays every ledger event from scratch.
The raw-node parsing and replay live in `ingest.py`; the schema and
row identity live in `models.py`'s declarative entities. This module
is the read half — `Store` and its ORM `select()` queries.

Query results are detached ORM instances: all columns are
materialized before the session closes, so reading them afterwards is
safe. None of today's entities declare a `relationship()`, though — if
one is added later, its attribute must be eager-loaded in the query
itself, or a caller reading it after the session closes will hit
`DetachedInstanceError`.
"""

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import cast

from sqlalchemy import CursorResult, Engine, create_engine, exists, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ghmem.aliases import DiscussionNumber, GraphQLId, IssueOrPRNumber
from ghmem.exceptions import (
    DiscussionNotFoundError,
    IssueOrPRNotFoundError,
    PRDiffNotFoundError,
)
from ghmem.heat_query import heat_scores
from ghmem.ingest import apply_routing_event, rebuild_from_ledgers
from ghmem.ledger import DispatchedEvent, FetchEvent, Ledger, RoutingEvent
from ghmem.models import (
    Comment,
    Commit,
    CrossRef,
    Discussion,
    DiscussionComment,
    Dispatch,
    FetchFailed,
    HeatScore,
    IssueOrPR,
    PRDiff,
    Reaction,
    Review,
    ReviewComment,
    Route,
    Topic,
)

__all__ = ["Store"]

_BUSY_TIMEOUT_SECONDS = 30
"""How long a writer waits on SQLite's lock before raising, not 5s.

`reserve_dispatch`'s conditional insert leans on SQLite's one-writer
lock to serialize concurrent reservations, so a busy-waiting writer is
the expected case, not a bug — set explicitly rather than trusting
pysqlite's 5-second default to still be right if it ever changes.
"""


class Store:
    """SQLAlchemy-backed mirror of the fetched GitHub corpus."""

    def __init__(self, engine: Engine) -> None:
        """Bind this store to a SQLAlchemy engine over a SQLite database."""
        self._engine = engine

    @classmethod
    def open(cls, path: Path) -> Store:
        """Open (creating if absent) the SQLite database at `path`."""
        engine = create_engine(
            f"sqlite:///{path}", connect_args={"timeout": _BUSY_TIMEOUT_SECONDS}
        )
        return cls(engine)

    def rebuild_from_ledgers(
        self, fetch: Ledger[FetchEvent], routing: Ledger[RoutingEvent]
    ) -> None:
        """Drop and repopulate every table by replaying both ledgers.

        Replay is idempotent: the whole schema is dropped and recreated
        first, and every row is upserted by id, so rebuilding twice from
        the same ledgers leaves identical query results.

        See `ingest.rebuild_from_ledgers` for the replay itself and the
        raw-node contract it depends on.
        """
        rebuild_from_ledgers(self._engine, fetch, routing)

    def record_routing_event(
        self, routing: Ledger[RoutingEvent], event: RoutingEvent
    ) -> None:
        """Record one routing event in both the ledger and the mirror.

        Every mutating dispatch command but `reserve_dispatch` goes
        through here, applying the event through the same
        `apply_routing_event` reducer that `rebuild_from_ledgers`
        replays — why those live writes cannot drift from a cold
        rebuild. `reserve_dispatch` is the exception; see its docstring.

        The three steps interleave deliberately. The reducer runs first,
        but inside an uncommitted transaction, so an event it rejects
        never reaches `routing.jsonl` — the one file here that cannot be
        regenerated, and the one replay is strict about. This is the
        parse-before-append rule the fetch driver follows via
        `validate_raw_node`, which the routing ledger needs more, not
        less: a poison line there makes every future rebuild raise,
        whereas a bad fetch line can simply be refetched.

        The ledger is then appended *before* the mirror commits, because
        it is the durable truth and the mirror is a regenerable cache. A
        crash in between loses nothing: the next rebuild replays the
        event that already landed. The reverse order could commit a
        verdict that no ledger records, which no rebuild would restore.

        Durability beyond the OS is the caller's checkpoint to take, via
        `Ledger.sync()` — an fsync per routed item would tax a triage
        session for a guarantee that only matters at run boundaries.
        """
        with Session(self._engine) as session:
            apply_routing_event(session, event)
            routing.append(event)
            session.commit()

    def session_usage(self, session_token: str) -> tuple[int, int]:
        """Return (items, chars) dispatched to `session_token` so far.

        Exact, not approximate, because of `Dispatch`'s item-id-only
        key: concurrent sessions are handed distinct items, and
        `requeue` only ever deletes a row with no session live, so
        every item a running session was dispatched still carries that
        session here for the whole session's lifetime.
        """
        with Session(self._engine) as session:
            items, chars = session.execute(
                select(
                    func.count(Dispatch.item_id),
                    func.coalesce(func.sum(Dispatch.packet_chars), 0),
                ).where(Dispatch.session == session_token)
            ).one()
        return (items, chars)

    def reserve_dispatch(
        self, routing: Ledger[RoutingEvent], event: DispatchedEvent
    ) -> bool:
        """Atomically reserve an item for a session; False if already taken.

        The live dispatch path, and the one place two triage sessions
        can race for the same item: `Dispatch` is keyed on `item_id`
        alone, so whichever session's row lands first wins, and the
        loser must learn that rather than silently overwrite the
        winner. A read-then-write can't enforce that — the `sqlite3`
        driver opens a transaction before a write but not before a
        read, so two processes can both read "free" and both write.
        This collapses the check and the write into one conditional
        insert (`INSERT ... ON CONFLICT (item_id) DO NOTHING`), which
        SQLite evaluates inside the single writer's transaction — there
        is no window between "checked" and "written" for a second
        writer to land in.

        This is why the insert here is conditional while
        `apply_routing_event`'s "dispatched" branch is an unconditional
        `session.merge`: replay re-applies events that already
        happened and must never decline one on a technicality, but the
        live path is exactly the technicality — declining a second
        reservation for the same item is the whole point.

        The ledger append sits between the insert and the commit,
        inside the same transaction, so SQLite's writer lock serializes
        it as a side effect of the insert: a second reservation cannot
        observe the ledger between another session's insert and its
        append. Moving the append outside the transaction would give
        that up.
        """
        data = event["data"]
        stmt = (
            sqlite_insert(Dispatch)
            .values(
                item_id=GraphQLId(data["item_id"]),
                session=data["session"],
                packet_chars=data["packet_chars"],
            )
            .on_conflict_do_nothing(index_elements=["item_id"])
        )
        with Session(self._engine) as session:
            # No `.returning()`, so mypy sees the untyped `Result` overload.
            reserved = cast("CursorResult[None]", session.execute(stmt)).rowcount == 1
            if reserved:
                routing.append(event)
                session.commit()
        return reserved

    def get_issue_or_pr(self, number: IssueOrPRNumber) -> IssueOrPR:
        """Return the issue or pull request numbered `number`.

        Raises `IssueOrPRNotFoundError` if no such row was ever fetched.
        """
        with Session(self._engine) as session:
            row = session.execute(
                select(IssueOrPR).where(IssueOrPR.number == number)
            ).scalar_one_or_none()
        if row is None:
            raise IssueOrPRNotFoundError(number)
        return row

    def issue_or_pr_by_id(self, item_id: GraphQLId) -> IssueOrPR | None:
        """Return the issue or pull request with graphql id `item_id`.

        Returns `None` if no such row was ever fetched — a cross-ref
        may point at an item never fetched into the corpus.
        """
        with Session(self._engine) as session:
            return session.execute(
                select(IssueOrPR).where(IssueOrPR.id == item_id)
            ).scalar_one_or_none()

    def all_issues_and_prs(self) -> list[IssueOrPR]:
        """Return every issue and pull request, ordered by number."""
        with Session(self._engine) as session:
            return list(
                session.execute(select(IssueOrPR).order_by(IssueOrPR.number)).scalars()
            )

    def heat_scores(self, weights: Mapping[str, float]) -> list[HeatScore]:
        """Score and rank every item by heat, hottest first."""
        return heat_scores(self._engine, weights)

    def comments_for(self, item_id: GraphQLId) -> list[Comment]:
        """Return the comments on `item_id`, oldest first."""
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(Comment)
                    .where(Comment.item_id == item_id)
                    .order_by(Comment.created_at)
                ).scalars()
            )

    def reviews_for(self, pr_id: GraphQLId) -> list[Review]:
        """Return the reviews on `pr_id`, oldest first."""
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(Review)
                    .where(Review.pr_id == pr_id)
                    .order_by(Review.submitted_at)
                ).scalars()
            )

    def review_comments_for(self, review_id: GraphQLId) -> list[ReviewComment]:
        """Return the comments left on `review_id`, oldest first."""
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(ReviewComment)
                    .where(ReviewComment.review_id == review_id)
                    .order_by(ReviewComment.created_at)
                ).scalars()
            )

    def reactions_for(self, subject_id: GraphQLId) -> list[Reaction]:
        """Return the reaction-content counts on `subject_id`."""
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(Reaction)
                    .where(Reaction.subject_id == subject_id)
                    .order_by(Reaction.content)
                ).scalars()
            )

    def cross_refs_for(self, item_id: GraphQLId) -> list[CrossRef]:
        """Return cross-references where `item_id` is the source or target."""
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(CrossRef).where(
                        or_(CrossRef.src_id == item_id, CrossRef.dst_id == item_id)
                    )
                ).scalars()
            )

    def pr_diff_for(self, number: IssueOrPRNumber) -> PRDiff:
        """Return the diff for the pull request numbered `number`.

        Raises `PRDiffNotFoundError` if no diff was ever fetched for it
        (including when `number` isn't a pull request at all).
        """
        with Session(self._engine) as session:
            diff = session.execute(
                select(PRDiff).where(PRDiff.pr_number == number)
            ).scalar_one_or_none()
        if diff is None:
            raise PRDiffNotFoundError(number)
        return diff

    def commits(self) -> list[Commit]:
        """Return every fetched commit, oldest first."""
        with Session(self._engine) as session:
            return list(
                session.execute(select(Commit).order_by(Commit.authored_date)).scalars()
            )

    def merges_before(self, d: date) -> list[IssueOrPR]:
        """Return merged pull requests merged strictly before `d`."""
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(IssueOrPR)
                    .where(IssueOrPR.merged_at.isnot(None))
                    .where(func.date(IssueOrPR.merged_at) < func.date(d.isoformat()))
                    .order_by(IssueOrPR.merged_at)
                ).scalars()
            )

    def issues_or_prs_authored_by(self, user: str) -> list[IssueOrPR]:
        """Return every issue or PR authored by `user`, ordered by number."""
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(IssueOrPR)
                    .where(IssueOrPR.author == user)
                    .order_by(IssueOrPR.number)
                ).scalars()
            )

    def get_discussion(self, number: DiscussionNumber) -> Discussion:
        """Return the discussion numbered `number`.

        Raises `DiscussionNotFoundError` if no such row was ever fetched.
        """
        with Session(self._engine) as session:
            row = session.execute(
                select(Discussion).where(Discussion.number == number)
            ).scalar_one_or_none()
        if row is None:
            raise DiscussionNotFoundError(number)
        return row

    def discussion_comments_for(
        self, discussion_id: GraphQLId
    ) -> list[DiscussionComment]:
        """Return the comments on `discussion_id`, oldest first."""
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(DiscussionComment)
                    .where(DiscussionComment.discussion_id == discussion_id)
                    .order_by(DiscussionComment.created_at, DiscussionComment.id)
                ).scalars()
            )

    def fetch_failures(self) -> list[FetchFailed]:
        """Return every fetch failure recorded in the ledger, by id."""
        with Session(self._engine) as session:
            return list(
                session.execute(select(FetchFailed).order_by(FetchFailed.id)).scalars()
            )

    def routes(self) -> list[Route]:
        """Return the current triage verdict of every item in the corpus.

        Constrained to fetched items, as `dispatched_unrouted` is: the
        routing ledger outlives the regenerable fetch ledger, so a
        verdict naming an absent item is a steady state, not corruption.

        Counting one mixes two populations; `--requeue` cannot serve one.
        """
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(Route)
                    .where(Route.item_id.in_(select(IssueOrPR.id)))
                    .order_by(Route.item_id)
                ).scalars()
            )

    def dispatched_unrouted(self) -> list[Dispatch]:
        """Return corpus items dispatched but carrying no current Route."""
        with Session(self._engine) as session:
            return list(
                session.execute(
                    select(Dispatch)
                    .where(Dispatch.item_id.in_(select(IssueOrPR.id)))
                    .where(~exists().where(Route.item_id == Dispatch.item_id))
                    .order_by(Dispatch.item_id)
                ).scalars()
            )

    def topics(self) -> list[Topic]:
        """Return every known topic, ordered by name."""
        with Session(self._engine) as session:
            return list(session.execute(select(Topic).order_by(Topic.name)).scalars())
