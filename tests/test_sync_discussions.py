"""Test the discussions sync driver."""

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
from ghmem.sync_core import _last_cursor, _Sync
from ghmem.sync_discussions import _DiscussionsQueryResponse, _sync_discussions
from tests.sync_helpers import _ALL_PAGES, TARGET, _ctx, _rebuild, _stub_graphql


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


def test_discussion_reactions_appended_with_injected_subject_id(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A discussion's `reactionGroups` become `reaction` events keyed to it.

    `show.render_discussion` renders a Reactions section, so the
    discussions connection has to select `reactionGroups` for it to
    have anything to render.

    Zero-count groups are dropped, as on issues and PRs: GitHub returns
    a group per reaction kind whether anyone used it or not.

    Comments and replies carry reactions too. Those are pinned by
    `test_discussion_comment_reactions_appended_with_injected_subject_id`
    and by
    `test_discussion_reply_reactions_appended_with_injected_subject_id`;
    this test covers only the discussion-level ones.

    When: `_sync_discussions` drains a discussions page
    Then: every non-empty reaction group is appended as a `reaction`
      naming its discussion as subject, and empty ones are not
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))

    reactions = {
        (event["data"]["subjectId"], event["data"]["content"]): event["data"]["count"]
        for event in fetch
        if event["type"] == "reaction"
    }
    assert reactions[("D_1", "THUMBS_UP")] == 3
    assert reactions[("D_3", "ROCKET")] == 1
    assert ("D_1", "HEART") not in reactions


def test_discussion_comment_reactions_appended_with_injected_subject_id(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A discussion comment's `reactionGroups` become a `reaction` event.

    Comment and reply reactions were never fetched before — only the
    discussion's own body reactions were.

    Zero-count groups are dropped, as everywhere else.

    When: `_sync_discussions` drains a discussion whose top-level
      comment carries reactions, and a follow-up-page comment (`DC_2`,
      fetched via `_drain_discussion_comments_follow_up`) whose
      reactions are non-empty too
    Then: a `reaction` event names each comment as subject, and a
      comment with only zero-count groups (`DC_1`) gets none
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))

    reactions = {
        (event["data"]["subjectId"], event["data"]["content"]): event["data"]["count"]
        for event in fetch
        if event["type"] == "reaction"
    }
    assert reactions[("DC_A", "THUMBS_UP")] == 1
    assert reactions[("DC_2", "HEART")] == 1
    assert ("DC_1", "HEART") not in reactions


def test_discussion_reply_reactions_appended_with_injected_subject_id(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A reply's reactions are appended from both replies pages.

    `DC_1_R1` arrives on the embedded first replies page. `DC_1_R2`
    arrives via the replies follow-up query — the `_drain_follow_up`
    call inside `_append_comment_replies`, a separate GraphQL selection
    this proves also carries `reactionGroups`.

    When: `_sync_discussions` drains a comment whose replies span two
      pages and both carry reactions
    Then: a `reaction` event names each reply as subject
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))

    reactions = {
        (event["data"]["subjectId"], event["data"]["content"]): event["data"]["count"]
        for event in fetch
        if event["type"] == "reaction"
    }
    assert reactions[("DC_1_R1", "ROCKET")] == 1
    assert reactions[("DC_1_R2", "LAUGH")] == 1


def test_discussion_comment_raw_node_carries_no_reaction_groups(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A `discussion_comment` event's `data` never carries `reactionGroups`.

    Reactions are emitted as their own `reaction` events keyed by
    `subjectId` — this pins the projection for both a top-level comment
    and a reply.

    When: `_sync_discussions` drains a discussion with a reacted-to
      comment and a reacted-to reply
    Then: neither's `data` dict has a `reactionGroups` key
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))

    comments = {
        event["data"]["id"]: event["data"]
        for event in fetch
        if event["type"] == "discussion_comment"
    }
    assert "reactionGroups" not in comments["DC_A"]
    assert "reactionGroups" not in comments["DC_1_R1"]


@pytest.mark.usefixtures("target_corpus")
def test_rendered_discussion_shows_its_reactions(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Reactions synced from a discussion reach its rendered output.

    The end the fetch gap was hiding: the renderer's Reactions section
    could never populate from real data, only from hand-authored
    fixtures.

    When: the store is rebuilt from a full discussions sync and
      discussion #100 is rendered
    Then: the output carries a Reactions section with its counts
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))
    store = _rebuild(fetch, tmp_path)

    rendered = render_discussion(store, build_standing(store), DiscussionNumber(100))

    assert "## Reactions" in rendered
    assert "THUMBS_UP\N{MULTIPLICATION SIGN}3" in rendered


@pytest.mark.usefixtures("target_corpus")
def test_cross_page_reply_parent_appended_first(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A reply's parent comment is always appended before the reply.

    Discussion #101's comments span two pages (`DC_1` on page 1,
    `DC_2` on page 2 via follow-up), and `DC_1`'s own replies also span
    two pages (`DC_1_R1` embedded, `DC_1_R2` via follow-up) — the shape
    that would strand a reply if comments and replies were drained out
    of order.

    Live-schema discovery found replies strictly nested under their own
    parent's `replies` connection, never flattened into the top-level
    `comments` connection or another comment's page. An orphan from
    pagination order alone is therefore impossible by construction
    here; this test proves it holds anyway.

    The ledger-order assertions are what make the name true. The
    absence of an `OrphanReplyWarning` is a weaker, downstream signal:
    `render_discussion` resolves parents from the rebuilt store, where
    append order no longer shows, so it would stay quiet even if the
    driver appended every reply first.

    When: a full discussions sync is drained, then discussion #101 is
      rendered from the rebuilt store
    Then: `DC_2` — a comment reached only through the comments
      follow-up page — is appended before any reply on the page before
      it, and rendering raises no `OrphanReplyWarning`
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))
    appended = [
        event["data"]["id"] for event in fetch if event["type"] == "discussion_comment"
    ]
    store = _rebuild(fetch, tmp_path)
    standing = build_standing(store)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        render_discussion(store, standing, DiscussionNumber(101))

    assert appended.index("DC_1") < appended.index("DC_1_R1")
    assert appended.index("DC_2") < appended.index("DC_1_R1")
    assert appended.index("DC_2") < appended.index("DC_1_R2")
    assert not any(issubclass(w.category, OrphanReplyWarning) for w in caught)


def test_null_discussion_recorded_and_page_still_advances(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A `null` discussion is recorded and skipped, and the cursor still lands.

    The same observed GraphQL gap the items connections already absorb.
    Letting the `None` through would raise before this page's `cursor`
    event was appended, so the next run would resume from the previous
    page, refetch this one, and abort on the same null — the connection
    stuck behind one bad node forever.

    When: the discussions connection's second page carries a `null`
      entry alongside a real discussion
    Then: sync records a `fetch_failed` for it, the real discussion
      still lands, and the page's cursor is persisted so a resume
      starts past it
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    _sync_discussions(_ctx(mocker, fetch, _ALL_PAGES))

    failures = [event["data"] for event in fetch if event["type"] == "fetch_failed"]
    assert [failure["id"] for failure in failures] == ["discussions[0]"]
    assert "null node" in failures[0]["reason"]
    cursor = _last_cursor(fetch, "discussions")
    assert cursor is not None
    assert cursor["top_level_cursor"] == "discussions-cursor-2"
    store = _rebuild(fetch, tmp_path)
    assert store.get_discussion(DiscussionNumber(102)).id == "D_3"


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
        _sync_discussions(_Sync(interrupted_client, fetch, Counter(), TARGET))

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

    _sync_discussions(_Sync(resumed_client, fetch, Counter(), TARGET))

    store = _rebuild(fetch, tmp_path)
    assert store.get_discussion(DiscussionNumber(100)).id == "D_1"
    assert store.get_discussion(DiscussionNumber(102)).id == "D_3"
