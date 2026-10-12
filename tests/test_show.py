"""Test the `show` single-thread markdown renderer and its CLI wiring."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ghmem.aliases import IssueOrPRNumber
from ghmem.cli import main
from ghmem.exceptions import IssueOrPRNotFoundError
from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.show import render
from ghmem.show_diff import DIFF_BODY_MAX_CHARS, _cut_diff_body
from ghmem.standing import StandingTable, build_standing
from ghmem.store import Store
from tests.diff_fixtures import (
    DIFF_HEADER_LINE,
    LINE_WIDTH,
    big_diff_text,
    diff_body_line,
    write_diff_corpus,
)

pytestmark = pytest.mark.usefixtures("target_corpus")

FIXTURE = Path(__file__).parent / "fixtures" / "show_corpus.jsonl"
_DIFF_STORE_LINES = 200


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


@pytest.fixture
def diff_store(tmp_path: Path) -> Store:
    """Return a Store rebuilt from a generated 200-line diff corpus.

    `show_corpus.jsonl` carries a seven-line sketch of a diff, far too
    short to cut on a whole-line boundary at any interesting cap.
    Generating a long one here lets the cut tests drive
    `diff_max_chars` across real line boundaries without touching the
    production valve.
    """
    corpus = tmp_path / "diff_fetch.jsonl"
    write_diff_corpus(corpus, diff_lines=_DIFF_STORE_LINES)
    store = Store.open(tmp_path / "diff_cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(corpus)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "diff_routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store


@pytest.fixture
def diff_standing_table(diff_store: Store) -> StandingTable:
    """Return the StandingTable derived from the generated diff store."""
    return build_standing(diff_store)


def _fenced_diff_body(output: str) -> str:
    """Return what `output`'s ```diff fence encloses, marker excluded.

    Slicing on the fences rather than on the truncation marker is what
    makes the length assertions mean the cap: the marker renders below
    the closing fence, so slicing up to it would count the fence into
    the body and overstate it by four characters.
    """
    return output.split("```diff\n", 1)[1].split("\n```", 1)[0]


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


def test_diff_body_cut_at_a_whole_line_within_the_cap(
    diff_store: Store, diff_standing_table: StandingTable
) -> None:
    """A cut diff body never overshoots the cap, and keeps only whole lines.

    When: a 200-line generated diff is rendered with `diff_max_chars` set
      to five lines' worth
    Then: the body never exceeds the cap — pinning the cut as a real
      ceiling rather than the first line boundary past it — every line
      it kept is whole and in order starting with DIFF_HEADER_LINE, and
      the truncation marker follows
    """
    cap = 5 * LINE_WIDTH
    output = render(
        diff_store, diff_standing_table, IssueOrPRNumber(1), diff_max_chars=cap
    )

    assert "… [diff truncated] …" in output
    body = _fenced_diff_body(output)
    assert len(body) <= cap
    assert len(body) > len(DIFF_HEADER_LINE)
    body_lines = body.split("\n")
    assert body_lines[0] == DIFF_HEADER_LINE
    for index, line in enumerate(body_lines[1:]):
        assert line == diff_body_line(index)


def test_truncation_marker_sits_below_the_closing_fence(
    diff_store: Store, diff_standing_table: StandingTable
) -> None:
    """The truncation marker is a note about the diff, not a line of it.

    A unified diff has no line beginning with `…`, so a marker inside
    the fence is a line the reader has to know to discount, and a
    `diff`-highlighting renderer styles it as patch text regardless.

    When: a PR whose diff body was cut is rendered
    Then: the marker follows the fence that closes the diff block, with
      nothing of the body after it
    """
    output = render(
        diff_store, diff_standing_table, IssueOrPRNumber(1), diff_max_chars=0
    )

    marker = output.index("… [diff truncated] …")
    assert output[:marker].endswith("```\n")


def test_diff_max_chars_zero_renders_the_file_list_and_no_body(
    diff_store: Store, diff_standing_table: StandingTable
) -> None:
    """A cap of zero keeps the diffstat but cuts the whole body away.

    When: the PR is rendered with `diff_max_chars=0`
    Then: no diff body line survives, the truncation marker is present,
      and the diffstat file list — the floor the fit computation
      measures against — still renders
    """
    output = render(
        diff_store, diff_standing_table, IssueOrPRNumber(1), diff_max_chars=0
    )

    assert "src/big.go" in output
    assert "+200" in output
    assert "… [diff truncated] …" in output
    assert diff_body_line(0) not in output


@pytest.mark.parametrize("cap", [-1, -5, -4000])
def test_negative_diff_max_chars_keeps_nothing(
    diff_store: Store, diff_standing_table: StandingTable, cap: int
) -> None:
    """A negative cap is treated like zero: the whole body is dropped.

    When: the PR is rendered with `diff_max_chars` set to a negative
      value
    Then: no diff body line survives and the diffstat file list still
      renders. This pins the explicit non-positive guard in
      `_cut_diff_body`: a raw `rfind` search with a negative `end`
      would read from the wrong end of `diff_text` and keep several
      thousand characters of body instead of none.
    """
    output = render(
        diff_store, diff_standing_table, IssueOrPRNumber(1), diff_max_chars=cap
    )

    assert "src/big.go" in output
    assert "… [diff truncated] …" in output
    assert diff_body_line(0) not in output


def test_cap_on_a_newline_index_keeps_the_whole_line_it_ends(
    diff_store: Store, diff_standing_table: StandingTable
) -> None:
    """A cap landing exactly on a newline index keeps the line it ends.

    When: the cap equals the character offset of a real newline in the
      generated diff — the header plus five whole body lines, rather
      than a value strictly between two newlines
    Then: the cut body is exactly that long. The newline at the cap
      counts as "at or before" it, so the line it terminates is kept,
      not dropped — the property the `+ 1` in `_cut_diff_body`'s
      `rfind` bound exists to guarantee.
    """
    cap = len(DIFF_HEADER_LINE) + 5 * LINE_WIDTH
    output = render(
        diff_store, diff_standing_table, IssueOrPRNumber(1), diff_max_chars=cap
    )

    body = _fenced_diff_body(output)
    assert len(body) == cap


@pytest.mark.parametrize("cap", [0, 5 * LINE_WIDTH, 50, 3000])
def test_packet_length_is_floor_plus_cut_body_length(
    diff_store: Store, diff_standing_table: StandingTable, cap: int
) -> None:
    """Rendering at any cap costs exactly the floor plus the cut body.

    When: the same PR is rendered at the floor (`diff_max_chars=0`) and
      again at `cap`
    Then: the difference in rendered length equals exactly the cut
      body's own length. This is the identity a fit computation relies
      on: `_render_diff` appends the (possibly empty) body
      unconditionally, so a caller can measure the floor once and grow
      it by precisely the cut body's length to size a packet against a
      budget.
    """
    floor = render(
        diff_store, diff_standing_table, IssueOrPRNumber(1), diff_max_chars=0
    )
    at_cap = render(
        diff_store, diff_standing_table, IssueOrPRNumber(1), diff_max_chars=cap
    )
    cut_body, _ = _cut_diff_body(big_diff_text(_DIFF_STORE_LINES), cap)

    assert len(at_cap) == len(floor) + len(cut_body)


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


def test_cli_show_unknown_number_exits_nonzero(target_corpus: Path) -> None:
    """`ghmem show` on an unknown number exits non-zero, without a traceback.

    When: the corpus has been rebuilt but the requested number is absent
    Then: the CLI exits non-zero and prints no traceback
    """
    (target_corpus / "fetch.jsonl").write_text(FIXTURE.read_text())
    runner = CliRunner()
    runner.invoke(main, ["rebuild"])

    result = runner.invoke(main, ["show", "999"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_diff_body_max_chars_is_a_safety_valve() -> None:
    """The provisional valve is sized to spare ordinary diffs, not fixtures.

    When: DIFF_BODY_MAX_CHARS is imported
    Then: it's large enough that an ordinary PR diff renders whole (the
      median PR touches 16 lines), small enough to stay a fraction of
      one batch rather than a ceiling on real diffs
    """
    assert 10_000 <= DIFF_BODY_MAX_CHARS <= 50_000
