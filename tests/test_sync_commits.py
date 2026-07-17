"""Tests for the commit-history sync driver."""

from collections import Counter
from pathlib import Path

from pytest_mock import MockerFixture

from ghmem.github import GitHubClient
from ghmem.ledger import FetchEvent, Ledger
from ghmem.sync_commits import _sync_commits
from ghmem.sync_core import _Sync
from tests.sync_helpers import _ALL_PAGES, _ctx, _stub_graphql


def test_commit_history_maps_oid_and_author_name(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Commit nodes map `oid` to `sha` and `author.name` to a bare string.

    When: `_sync_commits` drains the default branch's commit history
    Then: each `commit` event's `sha` is the GraphQL `oid`, and
      `author` is the GitActor's bare display name, not `{"login": ...}`
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_commits(_ctx(mocker, fetch, _ALL_PAGES))

    commits = [event["data"] for event in fetch if event["type"] == "commit"]
    assert len(commits) == 2
    assert commits[0]["sha"] == "def456abc789"
    assert commits[0]["author"] == "Octavia Reviewer"
    assert commits[1]["sha"] == "abc123def456"
    assert commits[1]["author"] == "David Allouche"


def test_commit_data_omits_committed_date(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """`committedDate` drives the hwm but is not part of the contract.

    `_CommitNode` selects it so `since:` has a date to filter on, but
    `_RawCommit` forbids unknown fields, so it must not be appended.

    When: `_sync_commits` drains a commit-history page
    Then: no `commit` event's `data` carries a `committedDate` key
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_commits(_ctx(mocker, fetch, _ALL_PAGES))

    commits = [event["data"] for event in fetch if event["type"] == "commit"]
    assert all("committedDate" not in commit for commit in commits)


def test_commit_hwm_is_newest_committed_date(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """The commits hwm tracks `committedDate`, the date `since:` filters on.

    `history(since:)` filters on committedDate, not authoredDate —
    verified against the live API, where 633 of micro's ~3530 commits
    carry the two dates apart. The fixture's newest commit is
    deliberately one of those: authored 2026-05-09, committed
    2026-05-20. A hwm taken from `authoredDate` would silently rewind
    the next catch-up by eleven days.

    When: `_sync_commits` drains a commit-history page
    Then: the persisted cursor's `updated_at_hwm` is the newest
      `committedDate`, not any `authoredDate`
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_commits(_ctx(mocker, fetch, _ALL_PAGES))

    cursors = [event["data"] for event in fetch if event["type"] == "cursor"]
    assert cursors[0]["updated_at_hwm"] == "2026-05-20T09:00:00Z"


def test_commit_catch_up_passes_since_not_cursor(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A second run re-enters commit history at its head via `since:`.

    `Commit.history` is newest-first, so resuming from the persisted
    cursor would drain further into the *past* and never see a new
    commit. Catching up means `after: None` with `since:` set to the
    last hwm.

    When: `_sync_commits` runs again over a ledger whose commits drain
      already completed
    Then: the follow-up query passes `since` = the persisted hwm and
      leaves `after` unset
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    _sync_commits(_ctx(mocker, fetch, _ALL_PAGES))

    client = GitHubClient("tok")
    graphql = mocker.patch.object(
        client, "graphql", side_effect=_stub_graphql(_ALL_PAGES)
    )
    _sync_commits(_Sync(client, fetch, Counter()))

    variables = graphql.call_args_list[0].args[1]
    assert variables["since"] == "2026-05-20T09:00:00Z"
    assert variables["after"] is None
