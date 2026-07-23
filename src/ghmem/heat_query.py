"""Heat-ranking query: the one aggregate select behind `Store.heat_scores`.

Split out of `store.py` for size, not for a difference in role — this
is still the read half of the store, just the one query large enough
to earn its own file.

`Store.heat_scores` delegates here, mirroring how
`Store.rebuild_from_ledgers` delegates to `ingest.py`.
"""

from collections.abc import Collection, Mapping

from sqlalchemy import (
    Engine,
    Integer,
    Select,
    cast,
    desc,
    exists,
    func,
    select,
    union,
    union_all,
)
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect

from ghmem.models import (
    Base,
    Comment,
    CrossRef,
    Dispatch,
    HeatScore,
    IssueOrPR,
    Reaction,
    Review,
    ReviewComment,
    Route,
)


def heat_scores(engine: Engine, weights: Mapping[str, float]) -> list[HeatScore]:
    """Score and rank every item by heat, hottest first.

    This is one aggregate query: the per-metric counts are correlated
    subqueries, `weights` binds into the weighted sum that SQL sorts
    on, and ties break by number ascending.
    """
    query, item = _ranked_query(weights)
    return _run(engine, query, item)


def untriaged_heat_scores(
    engine: Engine, weights: Mapping[str, float]
) -> list[HeatScore]:
    """Score and rank every item not yet triaged, hottest first.

    "Not yet triaged" excludes an item carrying a `Route` (it already
    has a verdict) or an outstanding `Dispatch` (some session is
    holding it right now), as two `NOT EXISTS` clauses on the same
    `item` alias `_ranked_query` builds its aggregate against.

    Neither needs the explicit `.correlate(item)` the subqueries in
    `_ranked_query` do: these are plain, non-compound `EXISTS`, which
    auto-correlation resolves to `item` on its own.
    """
    query, item = _ranked_query(weights)
    query = query.where(~exists().where(Route.item_id == item.id)).where(
        ~exists().where(Dispatch.item_id == item.id)
    )
    return _run(engine, query, item)


def heat_scores_for(
    engine: Engine, weights: Mapping[str, float], numbers: Collection[int]
) -> list[HeatScore]:
    """Score and rank only the items whose number is in `numbers`.

    A third entry point beside `untriaged_heat_scores`, filtering the
    same `_ranked_query` aggregate to a handful of items rather than
    scoring the whole corpus — the difference between a query run after
    every `close` and one avoided. An item in `numbers` that no corpus
    row carries simply does not come back; a discussion number never
    does, since this aggregate runs over `IssueOrPR` only.
    """
    query, item = _ranked_query(weights)
    query = query.where(item.number.in_(numbers))
    return _run(engine, query, item)


_DAYS_PER_YEAR = 365.25


def _ranked_query(
    weights: Mapping[str, float],
) -> tuple[Select[tuple[object, ...]], type[IssueOrPR]]:
    """Build the heat-ranking select and the `item` alias it's built on.

    Unordered and unfiltered: `item` comes back alongside the `Select`
    so a caller can add a `WHERE` correlated on the same alias before
    ordering and executing it — `heat_scores` is the plain case,
    `untriaged_heat_scores` adds two exclusions.

    Weights are parameters rather than constants so recalibrating them
    never touches this SQL.
    """
    # Every subquery below correlates on `item` explicitly:
    # auto-correlation does not reach inside a compound select or a
    # nested IN, and silently cross-joins `item` back in instead.
    item = aliased(IssueOrPR, name="item")
    reviews = select(Review.id).where(Review.pr_id == item.id).correlate(item)

    comments = (
        _count_where(Comment, Comment.item_id == item.id)
        + _count_where(Review, Review.pr_id == item.id)
        + _count_where(ReviewComment, ReviewComment.review_id.in_(reviews))
    )
    # Reactions are scoped to the whole thread, like `comments` and
    # `participants` below: consensus often lands on a reply rather
    # than on the body, and counting only the body would rank exactly
    # those threads cold.
    subjects = union_all(
        select(IssueOrPR.id.label("id")).where(IssueOrPR.id == item.id).correlate(item),
        select(Comment.id).where(Comment.item_id == item.id).correlate(item),
        select(Review.id).where(Review.pr_id == item.id).correlate(item),
        select(ReviewComment.id)
        .where(ReviewComment.review_id.in_(reviews))
        .correlate(item),
    ).subquery()
    reactions = func.coalesce(
        select(func.sum(Reaction.count))
        .where(Reaction.subject_id.in_(select(subjects.c.id)))
        .scalar_subquery(),
        0,
    )
    authors = union(
        select(IssueOrPR.author).where(IssueOrPR.id == item.id).correlate(item),
        select(Comment.author).where(Comment.item_id == item.id).correlate(item),
        select(Review.author).where(Review.pr_id == item.id).correlate(item),
        select(ReviewComment.author)
        .where(ReviewComment.review_id.in_(reviews))
        .correlate(item),
    ).subquery()
    participants = select(func.count()).select_from(authors).scalar_subquery()

    # Distinct related items, not rows: one relationship is recorded
    # twice whenever a PR closes an issue — once as `closes` from
    # `closingIssuesReferences`, once as `cross_referenced` from the
    # issue's timeline. Counting rows would give every closing link
    # twice the count of a bare mention.
    #
    # The metric is deliberately not named `xref_degree`: degree is an
    # edge count, and edges equal neighbours only in a simple graph —
    # `CrossRef` is keyed on `(src, dst, kind)`, so this one is a
    # multigraph.
    neighbours = union(
        select(CrossRef.dst_id.label("id"))
        .where(CrossRef.src_id == item.id)
        .correlate(item),
        select(CrossRef.src_id).where(CrossRef.dst_id == item.id).correlate(item),
    ).subquery()
    related_items = select(func.count()).select_from(neighbours).scalar_subquery()
    activity = union_all(
        select(IssueOrPR.created_at.label("ts"))
        .where(IssueOrPR.id == item.id)
        .correlate(item),
        select(Comment.created_at).where(Comment.item_id == item.id).correlate(item),
        select(Review.submitted_at).where(Review.pr_id == item.id).correlate(item),
        select(ReviewComment.created_at)
        .where(ReviewComment.review_id.in_(reviews))
        .correlate(item),
    ).subquery()
    # max() over julianday, not over the stored text: timestamps are
    # ISO-8601 with an offset, and comparing them as strings ranks by
    # local wall clock rather than by instant.
    last_activity = select(func.max(func.julianday(activity.c.ts))).scalar_subquery()
    # Whole days, matching `timedelta.days`. The difference is never
    # negative because `item.created_at` is itself in `activity` —
    # CAST truncates toward zero where `.days` floors, so narrowing
    # that union would reintroduce a discrepancy.
    span_years = (
        cast(last_activity - func.julianday(item.created_at), Integer) / _DAYS_PER_YEAR
    )

    score = (
        weights["comments"] * comments
        + weights["reactions"] * reactions
        + weights["participants"] * participants
        + weights["related_items"] * related_items
        + weights["span_years"] * span_years
    )

    return (
        select(
            item.number,
            item.title,
            score.label("score"),
            comments.label("comments"),
            reactions.label("reactions"),
            participants.label("participants"),
            related_items.label("related_items"),
            span_years.label("span_years"),
        ),
        item,
    )


def _run(
    engine: Engine, query: Select[tuple[object, ...]], item: type[IssueOrPR]
) -> list[HeatScore]:
    """Order `query` by score, run it, and map its rows to `HeatScore`."""
    with Session(engine) as session:
        rows = session.execute(query.order_by(desc("score"), item.number)).all()
    return [
        HeatScore(
            number=row.number,
            title=row.title,
            score=row.score,
            comments=row.comments,
            reactions=row.reactions,
            participants=row.participants,
            related_items=row.related_items,
            span_years=row.span_years,
        )
        for row in rows
    ]


def _count_where(
    entity: type[Base], predicate: ColumnElement[bool]
) -> ScalarSelect[int]:
    """Return a correlated subquery counting `entity` rows matching."""
    return select(func.count()).select_from(entity).where(predicate).scalar_subquery()
