"""Test the `ghmem triage` and `ghmem route` CLI commands.

`triage.py` owns the session loop's own mechanics and rejection
wording (`test_triage.py`, `test_triage_verdict.py`); this module
tests only the CLI plumbing wrapped around it — packet vs. message
routing to stdout/stderr, and the exit-code contract an orchestration
script reads.
"""

from pathlib import Path
from typing import NamedTuple

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ghmem import triage
from ghmem.aliases import GraphQLId
from ghmem.cli import main
from ghmem.models import Dispatch, Route, Topic

FIXTURE = Path(__file__).parent / "fixtures" / "queue_corpus.jsonl"


@pytest.fixture
def corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point $GHMEM_CORPUS_DIR at a rebuilt copy of the queue fixture."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FIXTURE.read_text())
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    assert CliRunner().invoke(main, ["rebuild"]).exit_code == 0
    return corpus


def _session_token(packet: str) -> str:
    """Pull the session token off a packet's first header line."""
    return packet.splitlines()[0].removeprefix("session: ")


def _item_number(packet: str) -> int:
    """Pull the served item's number off a packet's `item:` line."""
    for line in packet.splitlines():
        if line.startswith("item: "):
            return int(line.removeprefix("item: "))
    raise AssertionError(f"no 'item:' line in packet:\n{packet}")


class Served(NamedTuple):
    """An open triage session with its first item dispatched."""

    runner: CliRunner
    token: str
    number: int


@pytest.fixture
def served(corpus: Path) -> Served:
    """Open a triage session on the fixture corpus and serve one item.

    Every `route` test starts here: a runner, the token to route
    under, and the number of the item actually dispatched to it.
    Asking for this implies `corpus`, so a test needs to name that one
    as well only when it reads the sqlite mirror for a path.
    """
    runner = CliRunner()
    packet = runner.invoke(main, ["triage"])
    assert packet.exit_code == 0, packet.output
    return Served(runner, _session_token(packet.stdout), _item_number(packet.stdout))


def test_triage_on_fresh_corpus_prints_a_packet(corpus: Path) -> None:
    """A fresh corpus's first `triage` call serves a packet.

    When: `ghmem triage` runs against an untouched queue
    Then: exit 0, a packet on stdout, nothing on stderr
    """
    result = CliRunner().invoke(main, ["triage"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _item_number(result.stdout) in {1, 2, 3}


def test_triage_on_fully_routed_corpus_prints_the_terminal_message(
    served: Served,
) -> None:
    """A fully-routed corpus reports done, not an error.

    When: all three fixture items have been served and routed, and
      `ghmem triage` is called again
    Then: exit 0, empty stdout, "triage complete: 0 pending" on
      stderr — an expected outcome must not read as a failure
    """
    number = served.number
    for _ in range(3):
        result = served.runner.invoke(
            main, ["route", served.token, str(number), "some-topic", "note"]
        )
        if result.stdout:
            number = _item_number(result.stdout)

    result = served.runner.invoke(main, ["triage"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr.strip() == "triage complete: 0 pending"


def test_route_with_a_valid_verdict_prints_the_next_packet(
    corpus: Path, served: Served
) -> None:
    """A valid `route` records the verdict and serves the next item.

    Observes both halves: the served item's number advanced (a route
    that served the wrong packet, or re-served the item just routed,
    would pass a weaker check), and the verdict actually landed as a
    `Route` row (skipping the write would still serve the next item,
    since the queue advances on the `Dispatch` row alone).

    When: the hottest item is served, then routed with a valid verdict
    Then: exit 0, the printed packet names a different item than the
      one just routed, and the routed item carries its `Route`
    """
    first = served.number

    result = served.runner.invoke(
        main, ["route", served.token, str(first), "some-topic", "note"]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    second = _item_number(result.stdout)
    assert second != first

    engine = create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")
    with Session(engine) as session:
        routed = session.get(Route, GraphQLId(f"I_{first}"))
    assert routed is not None
    assert (routed.verdict, routed.note, routed.session) == (
        "some-topic",
        "note",
        served.token,
    )


def test_route_rejects_a_topic_verdict_with_no_note(served: Served) -> None:
    """A non-no-signal verdict with no note is an actual error.

    When: `route` is called with a topic verdict and no NOTE argument,
      for an item genuinely dispatched to this token
    Then: exit 1, empty stdout, the rejection on stderr
    """
    result = served.runner.invoke(
        main, ["route", served.token, str(served.number), "some-topic"]
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem route:" in result.stderr


def test_route_rejects_no_signal_carrying_a_note(served: Served) -> None:
    """A `no-signal` verdict carrying a note is an actual error.

    When: `route` is called with `no-signal` and a nonempty NOTE, for
      an item genuinely dispatched to this token
    Then: exit 1, empty stdout, the rejection on stderr
    """
    result = served.runner.invoke(
        main, ["route", served.token, str(served.number), "no-signal", "stray"]
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem route:" in result.stderr


def test_route_note_flag_reads_the_note_from_stdin(
    corpus: Path, served: Served
) -> None:
    """`--note` takes the note from stdin instead of the argument list.

    The shape a quoted heredoc feeds: prose full of characters no
    argument-level allowlist can pass, delivered with zero expansion.

    When: `route` is invoked with `--note` and the note on stdin
    Then: exit 0, and the recorded `Route` carries the piped text
    """
    note = "dmaluka's ruling on `b.Settings` & $HOME; see #3659 (merged)"

    result = served.runner.invoke(
        main,
        ["route", served.token, str(served.number), "some-topic", "--note"],
        input=note,
    )

    assert result.exit_code == 0
    engine = create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")
    with Session(engine) as session:
        routed = session.get(Route, GraphQLId(f"I_{served.number}"))
    assert routed is not None
    assert routed.note == note


def test_route_note_flag_strips_the_heredocs_trailing_newline(
    corpus: Path, served: Served
) -> None:
    """Surrounding whitespace is not part of the note.

    A heredoc body always ends in a newline the author did not type,
    so it cannot be allowed to reach the ledger.

    When: `route --note` is fed a note padded with newlines
    Then: the recorded note is the stripped text
    """
    result = served.runner.invoke(
        main,
        ["route", served.token, str(served.number), "some-topic", "--note"],
        input="\na note\n\n",
    )

    assert result.exit_code == 0
    engine = create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")
    with Session(engine) as session:
        routed = session.get(Route, GraphQLId(f"I_{served.number}"))
    assert routed is not None
    assert routed.note == "a note"


def test_route_note_flag_rejects_a_note_spanning_lines(served: Served) -> None:
    """A note is one line, because a topic's context renders one per line.

    `topic.py` appends the note to a single-line item bullet, so an
    embedded newline would silently split one item into two rows of
    the synthesis context.

    When: `route --note` is fed a note containing a line break
    Then: exit 1, empty stdout, the rejection on stderr
    """
    result = served.runner.invoke(
        main,
        ["route", served.token, str(served.number), "some-topic", "--note"],
        input="first line\nsecond line\n",
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem route:" in result.stderr


def test_route_note_flag_rejects_an_empty_note(served: Served) -> None:
    """`--note` with nothing behind it is malformed, not a silent no-op.

    Whichever verdict it accompanies, the command is wrong: one that
    takes a note has none, and one that takes no note should not have
    passed `--note` at all. `no-signal` is the case under test because
    it is the one that would otherwise accept it — an empty note is
    exactly what `record_verdict` requires of it, so nothing further
    down would object.

    When: `route --note` is fed a blank stdin under a `no-signal` verdict
    Then: exit 1, empty stdout, the rejection on stderr
    """
    result = served.runner.invoke(
        main,
        ["route", served.token, str(served.number), "no-signal", "--note"],
        input="  \n",
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem route:" in result.stderr


def test_route_rejects_the_note_flag_alongside_a_positional_note(
    served: Served,
) -> None:
    """Two notes are an ambiguity, not a merge.

    When: `route` is given both a NOTE argument and `--note`
    Then: exit 1, empty stdout, the rejection on stderr
    """
    result = served.runner.invoke(
        main,
        [
            "route",
            served.token,
            str(served.number),
            "some-topic",
            "positional",
            "--note",
        ],
        input="piped",
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem route:" in result.stderr


def test_route_rejects_a_number_dispatched_to_another_token(served: Served) -> None:
    """Routing an item dispatched to a different session is an actual error.

    When: item X is dispatched to one session, and `route` is called
      for X under a second, unrelated token
    Then: exit 1, empty stdout, the rejection on stderr
    """
    impostor_packet = served.runner.invoke(main, ["triage"])
    impostor = _session_token(impostor_packet.stdout)

    result = served.runner.invoke(
        main, ["route", impostor, str(served.number), "some-topic", "note"]
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem route:" in result.stderr


def test_full_lifecycle_matches_a_cold_rebuild(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live session's writes agree with a rebuild from what it wrote.

    This is Part A's `test_routing_live.py::test_live_writes_match_a_
    cold_rebuild`, run across a whole session instead of one event —
    the architecture's central claim, that a live write and a cold
    replay of the same ledger can never diverge, has to hold for the
    CLI session loop too, not just for one `record_routing_event` call.

    When: BATCH_ITEMS is patched to 2, a session opens with `triage`
      and routes items via `route` until the item cap ends the batch
      (leaving the third fixture item queued but never dispatched)
    Then: the Dispatch, Route, and Topic rows built live by that run —
      nonempty, so two empty mirrors can't pass this by accident — are
      identical to the same tables after `ghmem rebuild` replays the
      fetch and routing ledgers that run just wrote
    """
    # Not the `served` fixture: BATCH_ITEMS has to be patched before the
    # session opens, and a fixture would have opened it already.
    monkeypatch.setattr(triage, "BATCH_ITEMS", 2)
    runner = CliRunner()
    served = runner.invoke(main, ["triage"])
    assert served.exit_code == 0, served.output
    token = _session_token(served.stdout)
    number = _item_number(served.stdout)

    result = runner.invoke(main, ["route", token, str(number), "some-topic", "note"])
    assert result.exit_code == 0, result.output
    assert result.stdout, "expected a second packet before the batch ends"
    number = _item_number(result.stdout)

    result = runner.invoke(main, ["route", token, str(number), "some-topic", "note"])
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr.strip() == "batch complete: budget exhausted"

    engine = create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")
    with Session(engine) as session:
        live = (
            list(session.execute(select(Dispatch)).scalars()),
            list(session.execute(select(Route)).scalars()),
            list(session.execute(select(Topic)).scalars()),
        )

    rebuilt_result = runner.invoke(main, ["rebuild"])
    assert rebuilt_result.exit_code == 0, rebuilt_result.output

    with Session(engine) as session:
        rebuilt = (
            list(session.execute(select(Dispatch)).scalars()),
            list(session.execute(select(Route)).scalars()),
            list(session.execute(select(Topic)).scalars()),
        )

    dispatches, routes, topics = live
    assert len(dispatches) == 2
    assert len(routes) == 2
    assert len(topics) == 1
    assert live == rebuilt
