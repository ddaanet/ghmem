"""Test the `show.render_discussion` renderer and its CLI wiring."""

import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from ghmem.aliases import DiscussionNumber
from ghmem.cli import main
from ghmem.exceptions import DiscussionNotFoundError
from ghmem.ledger import FetchEvent, Ledger
from ghmem.show import OrphanReplyWarning, render_discussion
from ghmem.standing import StandingTable, build_standing
from ghmem.store import RoutingEvent, Store

FIXTURE = Path(__file__).parent / "fixtures" / "discussion_corpus.jsonl"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from the discussion-corpus fixture ledger."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store


@pytest.fixture
def standing_table(store: Store) -> StandingTable:
    """Return the StandingTable derived from the discussion-corpus store."""
    return build_standing(store)


def test_render_discussion_golden(store: Store, standing_table: StandingTable) -> None:
    """Full markdown snapshot: header, body, nested comments, answer, reactions.

    When: discussion #1 is rendered
    Then: the output matches the expected markdown exactly — header with
      category and annotated author, body, chronological comments with
      the reply indented under its parent, the answer marked in place,
      and the reaction count
    """
    output = render_discussion(store, standing_table, DiscussionNumber(1))

    assert output == textwrap.dedent(
        """\
        # discussion #1 Why does wrapping jump at colorcolumn?

        - category: Q&A
        - author: asker1 (newcomer)

        Body of discussion one.

        ## Comments

        ### 2026-06-01T10:00:00+00:00 — commenter1 (newcomer)

        First top-level comment.

          ### 2026-06-01T10:30:00+00:00 — asker1 (newcomer)

          Reply to the first comment.

        **\N{CHECK MARK} Accepted answer**
        ### 2026-06-01T11:00:00+00:00 — answerer1 (newcomer)

        Second top-level comment, the accepted answer.

        ## Reactions

        - THUMBS_UP\N{MULTIPLICATION SIGN}2
        """
    )


def test_reply_indented_under_parent(
    store: Store, standing_table: StandingTable
) -> None:
    """A reply renders indented one level, immediately after its parent.

    When: discussion #1's DC_2 replies to top-level comment DC_1
    Then: the reply's heading is indented and appears after the parent's
      body but before the next top-level comment
    """
    output = render_discussion(store, standing_table, DiscussionNumber(1))

    parent_body = output.index("First top-level comment.")
    reply_heading = output.index("  ### 2026-06-01T10:30:00+00:00 — asker1")
    next_top_level = output.index("### 2026-06-01T11:00:00+00:00 — answerer1")
    assert parent_body < reply_heading < next_top_level


def test_orphan_reply_rendered_at_top_level_with_warning(tmp_path: Path) -> None:
    """A reply whose parent is absent still renders, un-indented, and warns.

    When: DC_9 on discussion #1 names parent DC_absent, which the corpus
      never fetched
    Then: OrphanReplyWarning is raised and the reply's body still appears,
      with its heading un-indented since it has no parent to nest under
    """
    lines = FIXTURE.read_text().splitlines()
    orphan = {
        "type": "discussion_comment",
        "data": {
            "id": "DC_9",
            "discussionId": "D_1",
            "parentId": "DC_absent",
            "author": {"login": "stray1"},
            "createdAt": "2026-06-01T12:00:00Z",
            "updatedAt": "2026-06-01T12:00:00Z",
            "body": "Reply to a comment we never fetched.",
        },
    }
    fetch_path = tmp_path / "fetch.jsonl"
    fetch_path.write_text("\n".join([*lines, json.dumps(orphan)]) + "\n")
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(fetch_path)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    standing = build_standing(store)

    with pytest.warns(OrphanReplyWarning, match="DC_absent"):
        output = render_discussion(store, standing, DiscussionNumber(1))

    assert "Reply to a comment we never fetched." in output
    assert "### 2026-06-01T12:00:00+00:00 — stray1" in output
    assert "  ### 2026-06-01T12:00:00+00:00 — stray1" not in output


def test_accepted_answer_marked(store: Store, standing_table: StandingTable) -> None:
    """The accepted answer is marked in place, not reordered.

    When: discussion #1's answer is its second top-level comment (DC_3)
    Then: the marker immediately precedes that comment's heading, and the
      comment still renders after the first top-level comment and its reply
    """
    output = render_discussion(store, standing_table, DiscussionNumber(1))

    reply_body = output.index("Reply to the first comment.")
    marker = output.index("**\N{CHECK MARK} Accepted answer**")
    answer_heading = output.index("### 2026-06-01T11:00:00+00:00 — answerer1")
    assert reply_body < marker < answer_heading


def test_no_answer_no_marker(store: Store, standing_table: StandingTable) -> None:
    """No accepted-answer marker appears when the discussion has none.

    When: discussion #2 has `answer_id is None`
    Then: no accepted-answer marker appears anywhere in the output
    """
    output = render_discussion(store, standing_table, DiscussionNumber(2))

    assert "Accepted answer" not in output
    assert "\N{CHECK MARK}" not in output


def test_unknown_number_raises(store: Store, standing_table: StandingTable) -> None:
    """Propagates DiscussionNotFoundError for an unknown number.

    When: render_discussion is called with a number absent from the corpus
    Then: DiscussionNotFoundError propagates, uncaught
    """
    with pytest.raises(DiscussionNotFoundError):
        render_discussion(store, standing_table, DiscussionNumber(999))


def test_discussion_command_not_found_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem discussion` on an unknown number exits 1 with a stderr message.

    When: the corpus has been rebuilt but the requested number is absent
    Then: the CLI exits 1 and prints the not-found message
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FIXTURE.read_text())
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    runner = CliRunner()
    runner.invoke(main, ["rebuild"])

    result = runner.invoke(main, ["discussion", "999"])

    assert result.exit_code == 1
    assert "ghmem discussion: no discussion numbered #999" in result.stderr
