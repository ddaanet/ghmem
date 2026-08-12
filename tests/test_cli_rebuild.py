"""Test the `ghmem rebuild` CLI command."""

from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ghmem.aliases import GraphQLId, IssueOrPRNumber
from ghmem.cli import main
from ghmem.ledger import Ledger, RoutingEvent
from ghmem.models import Dispatch, Topic
from ghmem.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "fetch_small.jsonl"


def test_rebuild_command_populates_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem rebuild` rebuilds the SQLite mirror in place.

    When: $GHMEM_CORPUS_DIR points at a corpus dir holding a fetch
      ledger and `ghmem rebuild` is invoked
    Then: the SQLite mirror at cache.sqlite is populated from it
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FIXTURE.read_text())
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))

    result = CliRunner().invoke(main, ["rebuild"])

    assert result.exit_code == 0, result.output
    store = Store.open(corpus / "cache.sqlite")
    assert (
        store.get_issue_or_pr(IssueOrPRNumber(1)).title
        == "Softwrap loses column alignment"
    )


def _stale_route_corpus(tmp_path: Path) -> Path:
    """Write a corpus where item #1 is routed, then refetched later."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(
        '{"type": "item", "data": {"id": "I_1", "__typename": "Issue", '
        '"number": 1, "title": "Item one", "state": "OPEN", '
        '"author": {"login": "alice"}, "createdAt": "2026-01-01T00:00:00Z", '
        '"updatedAt": "2026-01-01T00:00:00Z", "mergedBy": null, '
        '"mergedAt": null, "authorAssociation": "NONE", "body": "body one"}}\n'
        '{"type": "item", "data": {"id": "I_1", "__typename": "Issue", '
        '"number": 1, "title": "Item one", "state": "OPEN", '
        '"author": {"login": "alice"}, "createdAt": "2026-01-01T00:00:00Z", '
        '"updatedAt": "2026-01-05T00:00:00Z", "mergedBy": null, '
        '"mergedAt": null, "authorAssociation": "NONE", "body": "body one"}}\n',
        encoding="utf-8",
    )
    (corpus / "routing.jsonl").write_text(
        '{"type": "dispatched", "data": {"session": "s1", "item_id": "I_1", '
        '"packet_chars": 100, "fit": "fit", "diff_valve_dropped": 0}}\n'
        '{"type": "route", "data": {"session": "s1", "item_id": "I_1", '
        '"verdict": "softwrap", "note": "n", '
        '"item_updated_at": "2026-01-01T00:00:00Z"}}\n',
        encoding="utf-8",
    )
    return corpus


def test_rebuild_reenters_a_stale_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem rebuild` reenters an item whose corpus moved past its verdict.

    When: item #1 is dispatched, routed, then refetched with a later
      updatedAt (simulating a new comment's sync), and `ghmem rebuild`
      runs
    Then: its Dispatch row is freed, its topic is dirty, and the routing
      ledger gained a reenter event
    """
    corpus = _stale_route_corpus(tmp_path)
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))

    result = CliRunner().invoke(main, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert "reentered 1 item(s)" in result.output
    with Session(create_engine(f"sqlite:///{corpus / 'cache.sqlite'}")) as session:
        assert session.get(Dispatch, GraphQLId("I_1")) is None
        assert session.get(Topic, "softwrap").dirty is True  # type: ignore[union-attr]
    routing: Ledger[RoutingEvent] = Ledger(corpus / "routing.jsonl")
    reentered = [e for e in routing if e["type"] == "reenter"]
    assert reentered == [{"type": "reenter", "data": {"item_id": "I_1"}}]


def test_rebuild_does_not_reenter_an_already_freed_stale_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second `rebuild` does not reenter an item already reentered.

    When: `ghmem rebuild` runs twice in a row against the same
      still-stale item
    Then: the second run reenters 0 items — the first run already freed
      its Dispatch, and stale_routes' guard excludes it from then on
    """
    corpus = _stale_route_corpus(tmp_path)
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    first = CliRunner().invoke(main, ["rebuild"])
    assert first.exit_code == 0, first.output

    second = CliRunner().invoke(main, ["rebuild"])

    assert second.exit_code == 0, second.output
    assert "reentered 0 item(s)" in second.output
