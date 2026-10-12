"""Test the topic list a triage packet carries.

The rest of the session loop lives in `test_triage.py` and the fit
path in `test_triage_fit.py`; this module covers only the topic list
`serve_next` renders into a packet, and its accounting against
`BATCH_CHARS`.

An agent is told to check the packet's topic list for a near-duplicate
before minting a new topic name. Until the packet carried one, that
instruction had no data behind it: two concurrent sessions minted
`ssh-clipboard-copy-paste` and `clipboard-config` for the same ground
in one run, beside a `clipboard-behavior` that already existed.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ghmem import triage
from ghmem.aliases import IssueOrPRNumber
from ghmem.ledger import Ledger, RoutingEvent
from ghmem.models import Topic
from ghmem.store import Store
from tests.diff_fixtures import write_diff_corpus
from tests.triage_helpers import _engine, _store

pytestmark = pytest.mark.usefixtures("target_corpus")

_TOPICS_PREFIX = "topics: "

_DIFF_LINES = 500
"""Diff lines for the fit-path corpus, matching `test_triage_fit`'s size."""


def _topics_line(packet: str) -> str:
    """Return the packet's `topics:` line, or fail naming the packet."""
    for line in packet.splitlines():
        if line.startswith(_TOPICS_PREFIX):
            return line
    raise AssertionError(f"no 'topics:' line in packet:\n{packet[:400]}")


def _item_number(packet: str) -> IssueOrPRNumber:
    """Return the served item's number, off the packet's `item:` line."""
    for line in packet.splitlines():
        if line.startswith("item: "):
            return IssueOrPRNumber(int(line.removeprefix("item: ")))
    raise AssertionError(f"no 'item:' line in packet:\n{packet[:400]}")


def _packet_chars(packet: str) -> int:
    """Return the `packet-chars:` figure the packet reports for itself."""
    for line in packet.splitlines():
        if line.startswith("packet-chars: "):
            return int(line.removeprefix("packet-chars: "))
    raise AssertionError(f"no 'packet-chars:' line in packet:\n{packet[:400]}")


def _diff_store(root: Path, corpus: Path) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a store on `corpus` under `root`, carrying one topic `alpha`.

    Each call gets its own database and routing ledger, so measuring a packet's
    full size does not dispatch the item the test then wants served for real.
    """
    root.mkdir()
    db = root / "diff_cache.sqlite"
    store = Store.open(db)
    routing: Ledger[RoutingEvent] = Ledger(root / "routing.jsonl")
    store.rebuild_from_ledgers(Ledger(corpus), routing)
    with Session(create_engine(f"sqlite:///{db}")) as session:
        session.add(Topic(name="alpha", dirty=True))
        session.commit()
    return store, routing


def _serve(
    store: Store, routing: Ledger[RoutingEvent], token: str, packet_dir: Path
) -> str:
    """Serve one packet under `token`, failing on a terminal message."""
    result = triage.serve_next(store, routing, token, packet_dir)
    assert result.packet is not None, result.message
    return result.packet


def test_fresh_corpus_serves_an_empty_topic_list(tmp_path: Path) -> None:
    """A corpus with no topics still carries the line, marked empty.

    The line is unconditional so an agent never has to tell "no topics
    yet" from "the packet forgot to say" — the two would otherwise
    look identical, and the second is the failure this whole feature
    exists to stop.

    When: the first item of a never-routed corpus is served
    Then: the packet carries `topics: (none)`
    """
    store, routing = _store(tmp_path)

    packet = _serve(store, routing, triage.mint_session(), tmp_path / "packets")

    assert _topics_line(packet) == "topics: (none)"


def test_packet_lists_every_known_topic(tmp_path: Path) -> None:
    """A packet names each topic already minted, in `Store.topics` order.

    When: two items are routed to `beta` and `alpha`, then a third is
      served
    Then: the packet's topic line names both, name-ordered
    """
    store, routing = _store(tmp_path)
    token = triage.mint_session()
    for verdict in ("beta", "alpha"):
        packet = _serve(store, routing, token, tmp_path / "packets")
        triage.record_verdict(
            store, routing, token, _item_number(packet), verdict, "a note"
        )

    packet = _serve(store, routing, token, tmp_path / "packets")

    assert _topics_line(packet) == "topics: alpha, beta"


def test_topic_line_counts_against_packet_chars(tmp_path: Path) -> None:
    """The topic list is inside the budgeted body, not free header space.

    Measured as the delta against the same corpus with no topics
    minted, rather than against a formula this test shares with the
    code: a topic line rendered into the uncounted header would leave
    the two figures equal.

    When: the same item is served from a corpus with no topics and
      from one carrying `clipboard-behavior`, a name longer than the
      `(none)` marker it replaces
    Then: the reported `packet-chars` grows by exactly the length of
      the added topic-line text
    """
    bare_store, bare_routing = _store(tmp_path / "bare")
    bare = _serve(bare_store, bare_routing, triage.mint_session(), tmp_path / "packets")

    store, routing = _store(tmp_path / "routed")
    with Session(_engine(tmp_path / "routed")) as session:
        session.add(Topic(name="clipboard-behavior", dirty=True))
        session.commit()
    packet = _serve(store, routing, triage.mint_session(), tmp_path / "packets")

    grew = len(_topics_line(packet)) - len(_topics_line(bare))
    assert _packet_chars(packet) - _packet_chars(bare) == grew
    assert grew > 0


def test_oversized_first_item_accounts_for_topics_before_cutting_its_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The diff allowance is computed over a floor that already has the topics.

    The topic list must reach the packet through the same render the
    fit path measures its floor with. Appended afterwards instead, it
    would push a `diff_cut` packet past the very budget the cut was
    performed to respect.

    When: a first item whose packet exceeds a patched `BATCH_CHARS` is
      served from a corpus carrying a topic
    Then: the packet carries the topic line and reports no more
      characters than the budget
    """
    corpus = tmp_path / "diff_fetch.jsonl"
    write_diff_corpus(corpus, diff_lines=_DIFF_LINES)
    measure, routing_measure = _diff_store(tmp_path / "measure", corpus)
    full = _packet_chars(
        _serve(measure, routing_measure, triage.mint_session(), tmp_path / "packets")
    )
    budget = full - 200
    store, routing = _diff_store(tmp_path / "real", corpus)
    monkeypatch.setattr(triage, "BATCH_CHARS", budget)

    packet = _serve(store, routing, triage.mint_session(), tmp_path / "packets")

    assert _topics_line(packet) == "topics: alpha"
    assert _packet_chars(packet) <= budget
