"""Test record_verdict's rejection guards.

Split out of `test_triage.py`, which keeps `serve_next`'s own
mechanics — mint, serve, gate, reserve, race — and the one
`record_verdict` test that is really about the queue advancing, not
the guard.
"""

from pathlib import Path

import pytest

from ghmem import triage
from ghmem.aliases import IssueOrPRNumber
from ghmem.exceptions import TriageSessionError
from ghmem.ledger import NO_SIGNAL
from tests.triage_helpers import _ranked_numbers, _store


def test_topic_verdict_without_note_raises(tmp_path: Path) -> None:
    """A non-no-signal verdict with an empty note is rejected.

    The item is genuinely dispatched to this token first: otherwise
    the "not currently dispatched" guard would raise regardless of the
    note check, and a mutation that dropped the note check entirely
    would still pass this test for the wrong reason.

    When: record_verdict is called with a topic verdict and an empty
      note, for an item this token actually holds
    Then: TriageSessionError is raised
    """
    store, routing = _store(tmp_path)
    hottest = _ranked_numbers(tmp_path)[0]
    token = triage.mint_session()
    served = triage.serve_next(store, routing, token)
    assert served.packet is not None

    with pytest.raises(TriageSessionError):
        triage.record_verdict(store, routing, token, hottest, "topic", "")


def test_no_signal_with_note_raises(tmp_path: Path) -> None:
    """A no-signal verdict carrying a note is rejected.

    Dispatched to this token first, for the same reason as
    `test_topic_verdict_without_note_raises` above.

    When: record_verdict is called with NO_SIGNAL and a non-empty
      note, for an item this token actually holds
    Then: TriageSessionError is raised
    """
    store, routing = _store(tmp_path)
    hottest = _ranked_numbers(tmp_path)[0]
    token = triage.mint_session()
    served = triage.serve_next(store, routing, token)
    assert served.packet is not None

    with pytest.raises(TriageSessionError):
        triage.record_verdict(store, routing, token, hottest, NO_SIGNAL, "stray note")


def test_number_dispatched_to_a_different_token_raises(tmp_path: Path) -> None:
    """Routing an item dispatched to another session is rejected.

    When: item X is dispatched to session "holder", and record_verdict
      is called for X under a different token
    Then: TriageSessionError is raised, catching an agent routing an
      id it was never handed
    """
    store, routing = _store(tmp_path)
    hottest = _ranked_numbers(tmp_path)[0]
    holder = triage.mint_session()
    served = triage.serve_next(store, routing, holder)
    assert served.packet is not None
    impostor = triage.mint_session()

    with pytest.raises(TriageSessionError):
        triage.record_verdict(store, routing, impostor, hottest, "topic", "note")


def test_re_routing_an_already_routed_number_raises(tmp_path: Path) -> None:
    """A second verdict for an already-routed number is rejected.

    `Store.dispatched_unrouted` would have reported this number as
    belonging to nobody the moment it was routed, which is exactly the
    misleading path a targeted Dispatch lookup replaces: the item is
    still dispatched to this token, so the guard must name the real
    cause — already routed — not "not currently dispatched".

    When: the hottest item is served, routed, and record_verdict is
      called again for the same number under the same token
    Then: TriageSessionError is raised
    """
    store, routing = _store(tmp_path)
    hottest = _ranked_numbers(tmp_path)[0]
    token = triage.mint_session()
    served = triage.serve_next(store, routing, token)
    assert served.packet is not None
    triage.record_verdict(store, routing, token, hottest, "some-topic", "reasoning")

    with pytest.raises(TriageSessionError):
        triage.record_verdict(store, routing, token, hottest, "other-topic", "again")


def test_unknown_number_raises_triage_session_error(tmp_path: Path) -> None:
    """A number never fetched into the corpus raises TriageSessionError.

    Not IssueOrPRNotFoundError: a caller — Task 4's CLI included —
    should map one exception class for every way record_verdict can
    reject a request.

    When: record_verdict is called for a number absent from the
      fixture
    Then: TriageSessionError is raised
    """
    store, routing = _store(tmp_path)
    token = triage.mint_session()

    with pytest.raises(TriageSessionError):
        triage.record_verdict(
            store, routing, token, IssueOrPRNumber(999_999), "topic", "note"
        )
