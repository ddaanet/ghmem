"""Time-varying contributor standing, derived purely from the corpus.

Standing answers "how much authority did this user have as of this
date" from evidence already in the `Store` — merged-PR credit and
commit authorship — never from an agent's judgment or from
`author_association` (a self-reported GitHub label, not derived here).
`build_standing` pulls every merged PR and commit once into a
`StandingTable`; `of`/`annotation` then filter that snapshot by date in
Python, without re-querying the store.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from enum import StrEnum, auto

from ghmem.store import Store

OWNER_LOGIN = "zyedidia"  # PROVISIONAL — calibrated in subproject 4
MERGE_THRESHOLD = 10  # PROVISIONAL — calibrated in subproject 4
ACCEPTED_PR_THRESHOLD = 5  # PROVISIONAL — calibrated in subproject 4

__all__ = [
    "ACCEPTED_PR_THRESHOLD",
    "MERGE_THRESHOLD",
    "OWNER_LOGIN",
    "Standing",
    "StandingTable",
    "build_standing",
]


class Standing(StrEnum):
    """Contributor standing tiers, from highest to lowest authority."""

    OWNER = auto()
    MAINTAINER = auto()
    ESTABLISHED = auto()
    NEWCOMER = auto()


@dataclass(frozen=True, slots=True)
class StandingTable:
    """A snapshot of corpus evidence, queryable per-user and per-date.

    Each field maps a user login to the dates on which they earned one
    kind of evidence, ascending: `merges_credited` (PRs they merged),
    `accepted_prs` (PRs merged that they authored), `commit_dates`
    (commits they authored). `of`/`annotation` filter these lists by
    `<= when` in memory — the store is never re-queried after `build_standing`.

    **Known approximation**: `commit_dates` is keyed by `Commit.author`,
    a bare GitActor display name, not a GitHub login — it can miss a
    contributor whose commit name differs from their login. Accepted-PR
    count is the primary ESTABLISHED signal; commit authorship is
    supplementary. Reconciled in subproject 4.
    """

    merges_credited: dict[str, list[date]]
    accepted_prs: dict[str, list[date]]
    commit_dates: dict[str, list[date]]

    def of(self, user: str, when: date) -> Standing:
        """Return `user`'s standing as of `when` (inclusive)."""
        if user == OWNER_LOGIN:
            return Standing.OWNER
        if _count_upto(self.merges_credited.get(user, []), when) >= MERGE_THRESHOLD:
            return Standing.MAINTAINER
        accepted = _count_upto(self.accepted_prs.get(user, []), when)
        has_commit = any(d <= when for d in self.commit_dates.get(user, []))
        if accepted >= ACCEPTED_PR_THRESHOLD or has_commit:
            return Standing.ESTABLISHED
        return Standing.NEWCOMER

    def annotation(self, user: str, when: date) -> str:
        """Render the inline annotation for `user` as of `when`.

        Renders by standing class: `"{user} (owner)"`; `"{user}
        (maintainer since ~{year}; {n} merges at time of comment)"`
        where `year` is the calendar year of `user`'s MERGE_THRESHOLD-th
        credited merge (a fixed fact, independent of `when`) and `n` is
        the count of merges credited `<= when`; `"{user} (established
        contributor; {n} accepted PRs at time of comment)"` where `n` is
        the count of accepted PRs `<= when`; `"{user} (newcomer)"`.
        """
        standing = self.of(user, when)
        if standing is Standing.OWNER:
            return f"{user} (owner)"
        if standing is Standing.MAINTAINER:
            credited = self.merges_credited[user]
            since = credited[MERGE_THRESHOLD - 1].year
            count = _count_upto(credited, when)
            return (
                f"{user} (maintainer since ~{since}; {count} merges at time of comment)"
            )
        if standing is Standing.ESTABLISHED:
            count = _count_upto(self.accepted_prs.get(user, []), when)
            return (
                f"{user} (established contributor; "
                f"{count} accepted PRs at time of comment)"
            )
        return f"{user} (newcomer)"


def _count_upto(dates: list[date], when: date) -> int:
    """Count `dates` falling on or before `when`."""
    return sum(1 for d in dates if d <= when)


def build_standing(store: Store) -> StandingTable:
    """Snapshot every merged PR and commit in `store` into a `StandingTable`.

    Pulls all evidence once — `store.merges_before(date.max)` (every merged PR
    ever) and `store.commits()` — and groups it into the table's three per-user
    date lists. Both store calls return results ordered ascending by date, so
    each per-user sublist stays sorted without a separate sort.
    """
    merges_credited: dict[str, list[date]] = defaultdict(list)
    accepted_prs: dict[str, list[date]] = defaultdict(list)
    commit_dates: dict[str, list[date]] = defaultdict(list)

    for pr in store.merges_before(date.max):
        if pr.merged_at is None:
            continue  # merges_before guarantees this is set; defensive.
        merged_date = pr.merged_at.date()
        accepted_prs[pr.author].append(merged_date)
        if pr.merged_by is None:
            continue  # no merger to credit; the PR's author still is, above.
        merges_credited[pr.merged_by].append(merged_date)

    for commit in store.commits():
        commit_dates[commit.author].append(commit.authored_date.date())

    return StandingTable(
        merges_credited=dict(merges_credited),
        accepted_prs=dict(accepted_prs),
        commit_dates=dict(commit_dates),
    )
