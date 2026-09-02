"""Shared fixtures for the split triage test modules.

Not collected by pytest — the module carries no `test_` prefix. It is
imported by `test_triage` and `test_triage_verdict`, mirroring
`sync_helpers`'s role for the split sync test modules.
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from ghmem.aliases import IssueOrPRNumber
from ghmem.heat import HEAT_WEIGHTS
from ghmem.heat_query import untriaged_heat_scores
from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.models import Dispatch
from ghmem.show import render
from ghmem.standing import build_standing
from ghmem.store import Store

DB_NAME = "cache.sqlite"
FIXTURE = Path(__file__).parent / "fixtures" / "queue_corpus.jsonl"


def _store(tmp_path: Path) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a Store and its routing ledger, rebuilt from the queue fixture."""
    store = Store.open(tmp_path / DB_NAME)
    fetch: Ledger[FetchEvent] = Ledger(FIXTURE)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


def _engine(tmp_path: Path) -> Engine:
    """Return a fresh engine on the mirror `_store` built under `tmp_path`."""
    return create_engine(f"sqlite:///{tmp_path / DB_NAME}")


def _ranked_numbers(tmp_path: Path) -> list[IssueOrPRNumber]:
    """Return the fixture's untriaged items in canonical heat order.

    Computed independently of `triage.py` via the production
    `HEAT_WEIGHTS`, so these tests assert against the queue's real
    order rather than an order hand-derived from the fixture's comment
    counts, which need not agree with the full weighted formula.
    """
    scores = untriaged_heat_scores(_engine(tmp_path), HEAT_WEIGHTS)
    return [s.number for s in scores]


def _dispatch_row(tmp_path: Path, item_id: str) -> Dispatch | None:
    """Return the current Dispatch row for `item_id`, or None."""
    with Session(_engine(tmp_path)) as session:
        return session.get(Dispatch, item_id)


def _body_prefix(store: Store, number: IssueOrPRNumber) -> str:
    r"""Return a packet body's two lead lines: `item:` then `topics:`.

    Duplicated in miniature rather than imported: this is the test's
    own independent measure of what a packet's char cost should be, so
    it doesn't share a formula with the code it's checking. Every size
    measure across the triage test modules is built on this one, so a
    line added to the body is accounted in exactly one place here.
    """
    names = ", ".join(topic.name for topic in store.topics()) or "(none)"
    return f"item: {number}\ntopics: {names}\n"


def _content_only(store: Store, number: IssueOrPRNumber) -> str:
    """Render `number`'s packet body the way `triage._render_packet` does."""
    standing = build_standing(store)
    return _body_prefix(store, number) + render(store, standing, number)
