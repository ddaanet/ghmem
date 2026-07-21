"""Test the `show` single-thread markdown renderer and its CLI wiring."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ghmem.aliases import IssueOrPRNumber
from ghmem.cli import main
from ghmem.exceptions import IssueOrPRNotFoundError
from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.show import DIFF_BODY_MAX_CHARS, render
from ghmem.standing import StandingTable, build_standing
from ghmem.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "show_corpus.jsonl"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from the show-corpus fixture ledger."""
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store


@pytest.fixture
def standing_table(store: Store) -> StandingTable:
    """Return the StandingTable derived from the show-corpus store."""
    return build_standing(store)


def test_renders_standing_annotations(
    store: Store, standing_table: StandingTable
) -> None:
    """Every timeline author is annotated with their computed standing.

    When: PR #2 is rendered, with a newcomer author, an established
      commenter, and a maintainer reviewer
    Then: each author's line carries the matching standing annotation
    """
    output = render(store, standing_table, IssueOrPRNumber(2))

    assert "newdev (newcomer)" in output
    assert "estab1 (established contributor;" in output
    assert "maintainer1 (maintainer since ~2023;" in output


def test_timeline_is_chronological(store: Store, standing_table: StandingTable) -> None:
    """Comments and the review interleave by timestamp, not by kind.

    When: PR #2's thread has comment, review (11:00), comment (12:00)
    Then: the rendered order matches that timestamp order
    """
    output = render(store, standing_table, IssueOrPRNumber(2))

    first_comment = output.index("Please add tests.")
    review = output.index("LGTM, minor comments below.")
    second_comment = output.index("Thanks, updated.")
    assert first_comment < review < second_comment


def test_diff_file_list_always_present(
    store: Store, standing_table: StandingTable
) -> None:
    """The diffstat file list renders regardless of diff-body truncation.

    When: PR #2's diff has one changed file
    Then: its path and +/- counts appear in the rendered output
    """
    output = render(store, standing_table, IssueOrPRNumber(2))

    assert "src/foo.go" in output
    assert "+10" in output
    assert "-2" in output


def test_large_diff_body_truncated_with_marker(
    store: Store, standing_table: StandingTable
) -> None:
    """An oversized diff body is capped at a line boundary, with a marker.

    When: PR #2's diff text exceeds DIFF_BODY_MAX_CHARS
    Then: the body is cut at the first newline past the cap — so the last
      shown line is whole, never split mid-line — the truncation marker
      follows, and text past the cut is absent
    """
    output = render(store, standing_table, IssueOrPRNumber(2))

    assert "… [diff truncated] …" in output
    assert "ZZZ_TAIL_UNIQUE_MARKER_ZZZ" not in output
    body = output.split("```diff\n", 1)[1].split("\n… [diff truncated]", 1)[0]
    assert body.endswith("+padding line to exceed the display cap")


def test_cross_refs_resolved_to_number_and_title(
    store: Store, standing_table: StandingTable
) -> None:
    """A cross-ref to an in-corpus issue resolves to its number and title.

    When: PR #2 cross-references issue #1 with kind "closes"
    Then: the rendered cross-reference shows "#1: <title> (closes)"
    """
    output = render(store, standing_table, IssueOrPRNumber(2))

    assert "#1: Softwrap column drift (closes)" in output


def test_cross_refs_sorted_numerically(
    store: Store, standing_table: StandingTable
) -> None:
    """Same-kind cross-refs sort by target number, not lexically.

    When: PR #2 has two "references" cross-refs, to #10 and to #9
    Then: #9 renders before #10 (a lexical sort would put #10 first)
    """
    output = render(store, standing_table, IssueOrPRNumber(2))

    nine = output.index("#9: Gutter offset off by one (references)")
    ten = output.index("#10: Filler PR 10 (references)")
    assert nine < ten


def test_reactions_rendered(store: Store, standing_table: StandingTable) -> None:
    """Reactions render as content x count pairs.

    When: PR #2 has a HEART x1 and a THUMBS_UP x4 reaction
    Then: both appear in the rendered output
    """
    output = render(store, standing_table, IssueOrPRNumber(2))

    assert "HEART\N{MULTIPLICATION SIGN}1" in output
    assert "THUMBS_UP\N{MULTIPLICATION SIGN}4" in output


def test_reply_reactions_render_where_they_landed(
    store: Store, standing_table: StandingTable
) -> None:
    """A reply's reactions render on that reply, not in the item section.

    The thumbs on "I'd take a PR for this" are the strongest signal in
    a thread; folding them into the item's Reactions section would
    detach them from the sentence they endorse, which is the whole of
    their meaning to a triage agent.

    When: PR #2's comment C_1, review RV_1 and review comment RVC_1
      each carry reactions
    Then: each renders on its own entry, above the item's Reactions
      section, and the item section still shows only the body's own
    """
    output = render(store, standing_table, IssueOrPRNumber(2))

    comment = output.index("reactions: THUMBS_UP\N{MULTIPLICATION SIGN}12")
    review = output.index("reactions: ROCKET\N{MULTIPLICATION SIGN}3")
    review_comment = output.index("reactions: EYES\N{MULTIPLICATION SIGN}2")
    section = output.index("## Reactions")
    assert max(comment, review, review_comment) < section
    assert "THUMBS_UP\N{MULTIPLICATION SIGN}12" not in output[section:]


def test_issue_without_diff_omits_diff_section(
    store: Store, standing_table: StandingTable
) -> None:
    """An issue (no PR diff) renders with no diff section at all.

    When: issue #1 (not a pull request) is rendered
    Then: no "## Diff" heading appears in the output
    """
    output = render(store, standing_table, IssueOrPRNumber(1))

    assert "## Diff" not in output


def test_unknown_number_raises(store: Store, standing_table: StandingTable) -> None:
    """Render propagates IssueOrPRNotFoundError for an unknown number.

    When: render is called with a number absent from the corpus
    Then: IssueOrPRNotFoundError propagates, uncaught
    """
    with pytest.raises(IssueOrPRNotFoundError):
        render(store, standing_table, IssueOrPRNumber(999))


def test_cli_show_unknown_number_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem show` on an unknown number exits non-zero, without a traceback.

    When: the corpus has been rebuilt but the requested number is absent
    Then: the CLI exits non-zero and prints no traceback
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FIXTURE.read_text())
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    runner = CliRunner()
    runner.invoke(main, ["rebuild"])

    result = runner.invoke(main, ["show", "999"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_diff_body_max_chars_is_modest() -> None:
    """The provisional display cap is small enough to keep fixtures readable.

    When: DIFF_BODY_MAX_CHARS is imported
    Then: it's a few hundred characters, not an unbounded or tiny value
    """
    assert 0 < DIFF_BODY_MAX_CHARS < 1000
