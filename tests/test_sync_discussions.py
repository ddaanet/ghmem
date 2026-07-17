"""Tests for the discussions sync driver."""

import warnings
from collections import Counter
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from ghmem.aliases import DiscussionNumber
from ghmem.exceptions import RateLimitError
from ghmem.github import GitHubClient
from ghmem.ledger import FetchEvent, Ledger
from ghmem.show import OrphanReplyWarning, render_discussion
from ghmem.standing import build_standing
from ghmem.sync_core import _Sync
from ghmem.sync_discussions import _DiscussionsQueryResponse, _sync_discussions
from tests.sync_helpers import _ALL_PAGES, _ctx, _rebuild, _stub_graphql


def test_discussions_appended_with_injected_discussion_id(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A discussion comment's `data` carries its parent discussion's id.

    GraphQL never supplies `discussionId`; the driver attaches it from
    the discussion it drained the comments connection from.

    When: `_sync_discussions` drains a discussions page
    Then: every appended `discussion_comment` names the id of the
      discussion it was fetched under
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))

    comment = next(
        event["data"]
        for event in fetch
        if event["type"] == "discussion_comment" and event["data"]["id"] == "DC_A"
    )
    assert comment["discussionId"] == "D_1"


def test_reply_carries_parent_id(mocker: MockerFixture, tmp_path: Path) -> None:
    """A reply's `data` carries `parentId`; a top-level comment's doesn't.

    When: `_sync_discussions` drains a discussion whose comment has a
      reply
    Then: the reply's `data` names its parent comment's id, and the
      top-level comment's `data` has no `parentId` key
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))

    comments = {
        event["data"]["id"]: event["data"]
        for event in fetch
        if event["type"] == "discussion_comment"
    }
    assert comments["DC_1_R1"]["parentId"] == "DC_1"
    assert "parentId" not in comments["DC_1"]


def test_cross_page_reply_parent_appended_first(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A reply's parent comment is always appended before the reply.

    Discussion #101's comments span two pages (`DC_1` on page 1,
    `DC_2` on page 2 via follow-up), and `DC_1`'s own replies also span
    two pages (`DC_1_R1` embedded, `DC_1_R2` via follow-up) — the shape
    that would strand a reply if comments and replies were drained out
    of order. Live-schema discovery found replies strictly nested
    under their own parent's `replies` connection (never flattened
    into the top-level `comments` connection or another comment's
    page), so an orphan from pagination order alone is
    impossible-by-construction here; this test still proves it holds.

    When: the store is rebuilt from a full discussions sync and
      discussion #101 is rendered
    Then: rendering raises no `OrphanReplyWarning`
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))
    store = _rebuild(fetch, tmp_path)
    standing = build_standing(store)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        render_discussion(store, standing, DiscussionNumber(101))

    assert not any(issubclass(w.category, OrphanReplyWarning) for w in caught)


def test_answered_discussion_records_answer_id(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """An answered discussion's `answer` reshapes to the accepted comment's id.

    When: `_sync_discussions` drains a discussion whose `answer` names
      an accepted comment
    Then: the rebuilt discussion's `answer_id` is that comment's id
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))

    store = _rebuild(fetch, tmp_path)

    assert store.get_discussion(DiscussionNumber(102)).answer_id == "DC_3"


def test_discussion_resume_from_cursor(mocker: MockerFixture, tmp_path: Path) -> None:
    """`--resume` continues the discussions connection from its cursor.

    When: the discussions connection is interrupted by `RateLimitError`
      right after page 1 is fully drained and its cursor persisted,
      then resumed
    Then: the resumed call fetches only page 2 (via `after:
      "discussions-cursor-1"`), and rebuilding finds both pages'
      discussions with no gaps or duplicates
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    interrupted_client = GitHubClient("tok")
    mocker.patch.object(
        interrupted_client,
        "graphql",
        side_effect=_stub_graphql(
            _ALL_PAGES, fail_at=(_DiscussionsQueryResponse, "discussions-cursor-1")
        ),
    )

    with pytest.raises(RateLimitError):
        _sync_discussions(_Sync(interrupted_client, fetch, Counter()))

    # Deliberately omits the page-1 key: if resume ever starts discussions
    # over from `after=None` instead of the persisted cursor, the stub
    # raises KeyError instead of silently re-serving page 1.
    resumed_pages: dict[tuple[type, str | None], str] = {
        (_DiscussionsQueryResponse, "discussions-cursor-1"): "discussions_page2.json"
    }
    resumed_client = GitHubClient("tok")
    mocker.patch.object(
        resumed_client, "graphql", side_effect=_stub_graphql(resumed_pages)
    )

    _sync_discussions(_Sync(resumed_client, fetch, Counter()))

    store = _rebuild(fetch, tmp_path)
    assert store.get_discussion(DiscussionNumber(100)).id == "D_1"
    assert store.get_discussion(DiscussionNumber(102)).id == "D_3"
