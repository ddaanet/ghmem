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

from sqlalchemy import Engine, create_engine, func, or_, select
from sqlalchemy.orm import Session

from ghmem.aliases import DiscussionNumber, GraphQLId, IssueOrPRNumber
from ghmem.exceptions import (
    DiscussionNotFoundError,
    IssueOrPRNotFoundError,
    PRDiffNotFoundError,
)
from ghmem.heat_query import heat_scores
from ghmem.ingest import apply_routing_event, rebuild_from_ledgers
from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.models import (
    Comment,
    Commit,
    CrossRef,
    Discussion,
    DiscussionComment,
    FetchFailed,
    HeatScore,
    IssueOrPR,
    PRDiff,
    Reaction,
    Review,
    ReviewComment,
)

__all__ = ["Store"]


class Store:
    """SQLAlchemy-backed mirror of the fetched GitHub corpus."""

    def __init__(self, engine: Engine) -> None:
        """Bind this store to a SQLAlchemy engine over a SQLite database."""
        self._engine = engine

    @classmethod
    def open(cls, path: Path) -> Store:
        """Open (creating if absent) the SQLite database at `path`."""
        return cls(create_engine(f"sqlite:///{path}"))

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
        rebuild_from_ledgers(self._engine, fetch, routing)

    def record_routing_event(
        self, routing: Ledger[RoutingEvent], event: RoutingEvent
    ) -> None:
        """Record one routing event in both the ledger and the mirror.

        The single entry point for every mutating dispatch command, and
        the reason live writes cannot drift from a cold rebuild: this
        applies the event through the same `apply_routing_event` reducer
        that `rebuild_from_ledgers` replays, as an upsert rather than a
        rebuild.

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
