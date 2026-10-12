"""Test `topic.assemble`'s exclusion of `NO_SIGNAL` routes.

Split out of `test_topic` along the seam `test_triage`/`test_triage_verdict`
already use for their own module: these tests share `topic_helpers`
with `test_topic` but earn their own module rather than pushing
`test_topic` over the line-count cap.
"""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from ghmem.exceptions import TopicNotRoutedError
from ghmem.ledger import NO_SIGNAL
from ghmem.models import IssueOrPR
from ghmem.topic import assemble
from tests.topic_helpers import _engine, _route, _route_no_signal, _store


def test_topic_digest_excludes_no_signal_queried_by_that_name(
    tmp_path: Path,
) -> None:
    """`topic_digest` itself excludes `NO_SIGNAL`, apart from `routed_items`.

    [mutation anchor: `_topic_digest` stops excluding `NO_SIGNAL`
    routes.]

    `test_no_signal_is_not_a_topic` alone cannot pin this: `assemble`
    consults `routed_items` before ever calling `topic_digest`, so a
    `_topic_digest` that forgot the exclusion would never be reached.
    This test calls `Store.topic_digest` directly, with `NO_SIGNAL` as
    the name, so the digest computation is the only thing under test.

    When: I_1 is routed to `NO_SIGNAL`
    Then: `store.topic_digest(NO_SIGNAL)` equals the empty-payload
      digest — the same one an unrouted topic name would produce
    """
    store, routing = _store(tmp_path)
    _route_no_signal(store, routing, "I_1")

    digest = store.topic_digest(NO_SIGNAL)

    assert digest == hashlib.sha256(b"").hexdigest()


def test_no_signal_is_not_a_topic(tmp_path: Path) -> None:
    """`NO_SIGNAL` verdicts never form a topic assemble can synthesize.

    [mutation anchor: `_topic_digest`/`routed_items` stop excluding
    `NO_SIGNAL` routes.]

    When: I_1 is routed to `NO_SIGNAL` and assemble is called for
      topic name `NO_SIGNAL` itself
    Then: TopicNotRoutedError propagates and the ledger stays untouched
    """
    store, routing = _store(tmp_path)
    _route_no_signal(store, routing, "I_1")

    with pytest.raises(TopicNotRoutedError):
        assemble(store, routing, NO_SIGNAL)

    assert all(e["type"] != "snapshot" for e in routing)


@pytest.mark.usefixtures("target_corpus")
def test_no_signal_routes_excluded_from_a_real_topic(tmp_path: Path) -> None:
    """A `NO_SIGNAL` route never leaks into another topic's assembly.

    [mutation anchors: `_topic_digest` stops excluding `NO_SIGNAL`
    routes; `routed_items` stops excluding `NO_SIGNAL` routes.]

    When: I_1 is routed to "softwrap" and I_2 is routed to `NO_SIGNAL`,
      then assemble is called for "softwrap"
    Then: I_2's `#2` line is absent from the item index, and the
      snapshot digest equals an independent sha256 recomputed over
      only I_1 — the item actually routed to "softwrap"
    """
    store, routing = _store(tmp_path)
    _route(store, routing, "I_1", "note 1")
    _route_no_signal(store, routing, "I_2")

    output = assemble(store, routing, "softwrap")

    item_index = output.split("## Item index", 1)[1].split("## Threads", 1)[0]
    assert "#2 " not in item_index

    snapshot = next(e for e in routing if e["type"] == "snapshot")
    with Session(_engine(tmp_path)) as session:
        item = session.get(IssueOrPR, "I_1")
        assert item is not None
        updated_at = item.updated_at
    payload = f"I_1\t{updated_at.isoformat()}"
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert snapshot["data"]["digest"] == expected
