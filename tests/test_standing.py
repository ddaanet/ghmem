"""Test time-varying contributor standing derivation."""

from datetime import date
from pathlib import Path

from ghmem.ledger import FetchEvent, Ledger
from ghmem.standing import (
    ACCEPTED_PR_THRESHOLD,
    MERGE_THRESHOLD,
    Standing,
    build_standing,
)
from ghmem.store import RoutingEvent, Store

FIXTURE = Path(__file__).parent / "fixtures" / "standing_corpus.jsonl"

# The 10th (MERGE_THRESHOLD-th) PR credited to dmaluka in the fixture is
# merged on this date — the era boundary for dmaluka's MAINTAINER status.
_DMALUKA_THRESHOLD_DATE = date(2023, 1, 15)


def _rebuilt_store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from the standing fixture ledger."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store


def test_dmaluka_maintainer_after_threshold_not_before(tmp_path: Path) -> None:
    """Dmaluka becomes MAINTAINER only once the Nth credited merge lands.

    When: dmaluka's MERGE_THRESHOLD-th credited merge is dated 2023-01-15
    Then: standing the day before is not MAINTAINER, and standing on or
      after that date is MAINTAINER
    """
    table = build_standing(_rebuilt_store(tmp_path))

    before = table.of("dmaluka", date(2023, 1, 14))
    after = table.of("dmaluka", _DMALUKA_THRESHOLD_DATE)

    assert before is not Standing.MAINTAINER
    assert after is Standing.MAINTAINER


def test_joekar_maintainer(tmp_path: Path) -> None:
    """A second contributor can independently cross the maintainer bar.

    When: JoeKar is credited with MERGE_THRESHOLD merges by 2023-11-01
    Then: standing on that date is MAINTAINER
    """
    table = build_standing(_rebuilt_store(tmp_path))

    assert table.of("JoeKar", date(2023, 11, 1)) is Standing.MAINTAINER


def test_owner_is_owner(tmp_path: Path) -> None:
    """The repo owner is always OWNER, regardless of corpus evidence.

    When: standing is queried for the OWNER_LOGIN user, who has no
      merges, PRs, or commits in the fixture corpus at all
    Then: standing is OWNER at any date
    """
    table = build_standing(_rebuilt_store(tmp_path))

    assert table.of("zyedidia", date(2020, 1, 1)) is Standing.OWNER
    assert table.of("zyedidia", date.max) is Standing.OWNER


def test_accepted_prs_promote_to_established(tmp_path: Path) -> None:
    """Enough accepted PRs promote a contributor to ESTABLISHED.

    When: alice has ACCEPTED_PR_THRESHOLD merged PRs credited to her by
      2023-03-20, and no merges credited to her and no commits
    Then: standing on that date is ESTABLISHED, not MAINTAINER
    """
    table = build_standing(_rebuilt_store(tmp_path))

    assert table.of("alice", date(2023, 3, 20)) is Standing.ESTABLISHED


def test_accepted_pr_credit_survives_null_merged_by(tmp_path: Path) -> None:
    """A merged PR with no recorded merger still credits its author.

    When: carol has ACCEPTED_PR_THRESHOLD merged PRs, each with
      `mergedBy` null (no merger recorded) but `mergedAt` set
    Then: standing on the date of the last such merge is ESTABLISHED —
      accepted-PR credit was not dropped by the null-`merged_by` guard
    """
    table = build_standing(_rebuilt_store(tmp_path))

    assert table.of("carol", date(2023, 4, 19)) is Standing.ESTABLISHED


def test_commit_author_promotes_to_established(tmp_path: Path) -> None:
    """A single authored commit is enough to promote to ESTABLISHED.

    When: bob has one commit authored 2023-05-01 and no merged PRs at
      all (neither credited nor authored)
    Then: standing on or after that date is ESTABLISHED
    """
    table = build_standing(_rebuilt_store(tmp_path))

    assert table.of("bob", date(2023, 5, 1)) is Standing.ESTABLISHED


def test_unknown_user_is_newcomer(tmp_path: Path) -> None:
    """A user absent from every evidence list is a NEWCOMER.

    When: newperson has no merges, PRs, or commits anywhere in the
      corpus
    Then: standing is NEWCOMER at any date
    """
    table = build_standing(_rebuilt_store(tmp_path))

    assert table.of("newperson", date.max) is Standing.NEWCOMER


def test_maintainer_annotation_format(tmp_path: Path) -> None:
    """The maintainer annotation names the since-year and merge count.

    When: dmaluka crosses MERGE_THRESHOLD on 2023-01-15 (the year of
      the Nth credited merge), and has exactly MERGE_THRESHOLD merges
      credited by that same date
    Then: the annotation reads "dmaluka (maintainer since ~2023;
      {MERGE_THRESHOLD} merges at time of comment)"
    """
    table = build_standing(_rebuilt_store(tmp_path))

    annotation = table.annotation("dmaluka", _DMALUKA_THRESHOLD_DATE)

    assert annotation == (
        f"dmaluka (maintainer since ~2023; {MERGE_THRESHOLD} merges at time of comment)"
    )


def test_established_and_newcomer_annotation_format(tmp_path: Path) -> None:
    """Established and newcomer annotations render their own inline forms.

    When: alice has ACCEPTED_PR_THRESHOLD accepted PRs by 2023-03-20,
      and newperson has no corpus evidence at all
    Then: alice's annotation names the accepted-PR count, and
      newperson's annotation is the bare "(newcomer)" form
    """
    table = build_standing(_rebuilt_store(tmp_path))

    alice_annotation = table.annotation("alice", date(2023, 3, 20))
    newperson_annotation = table.annotation("newperson", date.max)

    assert alice_annotation == (
        f"alice (established contributor; "
        f"{ACCEPTED_PR_THRESHOLD} accepted PRs at time of comment)"
    )
    assert newperson_annotation == "newperson (newcomer)"
