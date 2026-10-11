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
from click.testing import CliRunner, Result
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
    monkeypatch.setenv("GHMEM_PACKET_DIR", str(tmp_path / "packets"))
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


def _route(
    runner: CliRunner, token: str, number: int, verdict: str, note: str
) -> Result:
    """Invoke `route` the only way it can be called: the note on stdin.

    `input` stands in for the quoted heredoc a worker writes — the
    trailing newline included, since a heredoc body always carries one.
    Tests about a malformed stdin invoke `route` directly instead, to
    keep the exact bytes they are about in the test that is about them.
    """
    return runner.invoke(
        main, ["route", token, str(number), verdict], input=f"{note}\n"
    )


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
        result = _route(served.runner, served.token, number, "some-topic", "note")
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

    result = _route(served.runner, served.token, first, "some-topic", "note")

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


def test_route_records_the_note_on_a_no_signal_verdict(
    corpus: Path, served: Served
) -> None:
    """A `no-signal` verdict keeps the reason it was written off for.

    The note has no topic to attach to, which is exactly why it has to
    land on the `Route` row: nothing downstream would otherwise hold
    why an item was passed over.

    When: `route` is called with `no-signal` and a note
    Then: exit 0, and the `Route` row carries verdict and note both
    """
    first = served.number

    result = _route(
        served.runner, served.token, first, "no-signal", "substance is in #1683"
    )

    assert result.exit_code == 0, result.output
    engine = create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")
    with Session(engine) as session:
        routed = session.get(Route, GraphQLId(f"I_{first}"))
    assert routed is not None
    assert (routed.verdict, routed.note) == ("no-signal", "substance is in #1683")


def test_route_reads_the_note_from_stdin(corpus: Path, served: Served) -> None:
    """The note comes off stdin, which is the only place it can come from.

    The shape a quoted heredoc feeds: prose full of characters no
    argument-level allowlist can pass, delivered with zero expansion.

    When: `route` is invoked with the note on stdin
    Then: exit 0, and the recorded `Route` carries the piped text
    """
    note = "dmaluka's ruling on `b.Settings` & $HOME; see #3659 (merged)"

    result = _route(served.runner, served.token, served.number, "some-topic", note)

    assert result.exit_code == 0
    engine = create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")
    with Session(engine) as session:
        routed = session.get(Route, GraphQLId(f"I_{served.number}"))
    assert routed is not None
    assert routed.note == note


def test_route_strips_the_heredocs_trailing_newline(
    corpus: Path, served: Served
) -> None:
    """Surrounding whitespace is not part of the note.

    A heredoc body always ends in a newline the author did not type,
    so it cannot be allowed to reach the ledger.

    When: `route` is fed a note padded with newlines
    Then: the recorded note is the stripped text
    """
    result = served.runner.invoke(
        main,
        ["route", served.token, str(served.number), "some-topic"],
        input="\na note\n\n",
    )

    assert result.exit_code == 0
    engine = create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")
    with Session(engine) as session:
        routed = session.get(Route, GraphQLId(f"I_{served.number}"))
    assert routed is not None
    assert routed.note == "a note"


def test_route_rejects_a_note_spanning_lines(served: Served) -> None:
    """A note is one line, because a topic's context renders one per line.

    `topic.py` appends the note to a single-line item bullet, so an
    embedded newline would silently split one item into two rows of
    the synthesis context.

    When: `route` is fed a note containing a line break
    Then: exit 1, empty stdout, the rejection on stderr
    """
    result = served.runner.invoke(
        main,
        ["route", served.token, str(served.number), "some-topic"],
        input="first line\nsecond line\n",
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem route:" in result.stderr


@pytest.mark.parametrize("verdict", ["some-topic", "no-signal"])
def test_route_rejects_a_blank_stdin(served: Served, verdict: str) -> None:
    """A `route` that fed no note is malformed, not a silent no-op.

    Parametrized over both verdict kinds because the rule's whole
    point is that it does not branch on the verdict: an item written
    off is a decision, and a decision with no reasoning behind it is
    not auditable later. `no-signal` is the case that would otherwise
    slip through — an empty note is exactly what nothing downstream
    would object to.

    When: `route` is fed a blank stdin
    Then: exit 1, empty stdout, the rejection on stderr
    """
    result = served.runner.invoke(
        main,
        ["route", served.token, str(served.number), verdict],
        input="  \n",
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem route:" in result.stderr


def test_route_refuses_a_note_passed_as_an_argument(
    corpus: Path, served: Served
) -> None:
    """There is no argument form to fall back on, and no silent fallback.

    A note passed as an argument is what corrupted this corpus once —
    the shell substituted a backticked fragment away before `route`
    ever saw it. Removing the argument only helps if the removal is
    load-bearing: a fourth argument has to be a usage error, not a
    note quietly ignored in favour of the stdin one.

    When: `route` is given a fourth argument, with a note on stdin too
    Then: exit 2, and no `Route` row was written for the item
    """
    result = served.runner.invoke(
        main,
        ["route", served.token, str(served.number), "some-topic", "an argument note"],
        input="a piped note\n",
    )

    assert result.exit_code == 2
    engine = create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")
    with Session(engine) as session:
        assert session.get(Route, GraphQLId(f"I_{served.number}")) is None


def test_route_rejects_a_number_dispatched_to_another_token(served: Served) -> None:
    """Routing an item dispatched to a different session is an actual error.

    When: item X is dispatched to one session, and `route` is called
      for X under a second, unrelated token
    Then: exit 1, empty stdout, the rejection on stderr
    """
    impostor_packet = served.runner.invoke(main, ["triage"])
    impostor = _session_token(impostor_packet.stdout)

    result = _route(served.runner, impostor, served.number, "some-topic", "note")

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

    result = _route(runner, token, number, "some-topic", "note")
    assert result.exit_code == 0, result.output
    assert result.stdout, "expected a second packet before the batch ends"
    number = _item_number(result.stdout)

    result = _route(runner, token, number, "some-topic", "note")
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
