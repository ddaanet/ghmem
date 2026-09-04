"""Test the `ghmem unroute` CLI command."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ghmem.aliases import GraphQLId
from ghmem.cli import main
from ghmem.models import Route
from ghmem.store import Store

FETCH_FIXTURE = Path(__file__).parent / "fixtures" / "routing_fetch.jsonl"


def _corpus(tmp_path: Path, routed: tuple[str, ...] = ("I_1", "I_2")) -> Path:
    """Write a corpus routing every item in `routed` to "softwrap"."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FETCH_FIXTURE.read_text(), encoding="utf-8")
    stamps = {"I_1": "2026-01-01T00:00:00Z", "I_2": "2026-01-02T00:00:00Z"}
    lines = [
        json.dumps(
            {
                "type": "route",
                "data": {
                    "session": "s1",
                    "item_id": item_id,
                    "verdict": "softwrap",
                    "note": "a note",
                    "item_updated_at": stamps[item_id],
                },
            }
        )
        for item_id in routed
    ]
    (corpus / "routing.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return corpus


def _run(
    monkeypatch: pytest.MonkeyPatch, corpus: Path, args: list[str]
) -> tuple[int, str]:
    """Rebuild the mirror under `corpus`, then invoke `main` with `args`."""
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    rebuilt = CliRunner().invoke(main, ["rebuild"])
    assert rebuilt.exit_code == 0, rebuilt.output
    result = CliRunner().invoke(main, args)
    return result.exit_code, result.output


def test_unroute_clears_the_named_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem unroute` withdraws the verdict of every number it is given.

    When: items #1 and #2 are routed and `ghmem unroute 1` is invoked
    Then: #1 carries no Route, #2 still does, and the count is reported
    """
    corpus = _corpus(tmp_path)

    code, output = _run(monkeypatch, corpus, ["unroute", "1"])

    assert code == 0, output
    assert output.strip() == "Unrouted 1 item(s)"
    store = Store.open(corpus / "cache.sqlite")
    assert store.route_for(GraphQLId("I_1")) is None
    assert store.route_for(GraphQLId("I_2")) is not None


def test_unroute_takes_several_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem unroute` accepts a batch of numbers in one invocation.

    When: items #1 and #2 are routed and `ghmem unroute 1 2` is invoked
    Then: neither carries a Route and the count reports both
    """
    corpus = _corpus(tmp_path)

    code, output = _run(monkeypatch, corpus, ["unroute", "1", "2"])

    assert code == 0, output
    assert output.strip() == "Unrouted 2 item(s)"
    store = Store.open(corpus / "cache.sqlite")
    assert store.routes() == []


def test_unroute_appends_one_event_per_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each named item gets exactly one `unroute` line in the routing ledger.

    When: `ghmem unroute 1 2` is invoked over a two-route corpus
    Then: routing.jsonl gains two unroute events, one per item id, and
      loses none of its route lines
    """
    corpus = _corpus(tmp_path)

    code, output = _run(monkeypatch, corpus, ["unroute", "1", "2"])

    assert code == 0, output
    events = [
        json.loads(line)
        for line in (corpus / "routing.jsonl").read_text().splitlines()
        if line
    ]
    assert [e["data"]["item_id"] for e in events if e["type"] == "unroute"] == [
        "I_1",
        "I_2",
    ]
    assert sum(e["type"] == "route" for e in events) == 2


def test_unroute_rejects_an_unknown_number_before_appending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unknown number is refused with nothing written to the ledger.

    The whole batch is resolved before any event is appended, so a typo
    at the end of a long list cannot leave a half-applied correction.

    When: `ghmem unroute 1 99` is invoked and no item #99 exists
    Then: exit 1, the same not-found line `show` and `discussion`
      print, no unroute line in the ledger, and #1 still routed
    """
    corpus = _corpus(tmp_path)
    before = (corpus / "routing.jsonl").read_text()

    code, output = _run(monkeypatch, corpus, ["unroute", "1", "99"])

    assert code == 1
    assert "ghmem unroute: no issue or pull request numbered #99" in output
    assert (corpus / "routing.jsonl").read_text() == before
    store = Store.open(corpus / "cache.sqlite")
    assert store.route_for(GraphQLId("I_1")) is not None


def test_unroute_requires_at_least_one_number(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem unroute` with no numbers is a usage error, not a silent no-op.

    When: `ghmem unroute` is invoked bare
    Then: it exits non-zero and writes nothing
    """
    corpus = _corpus(tmp_path)
    before = (corpus / "routing.jsonl").read_text()

    code, _output = _run(monkeypatch, corpus, ["unroute"])

    assert code != 0
    assert (corpus / "routing.jsonl").read_text() == before


def test_unroute_twice_is_idempotent_and_rebuilds_the_same(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second `ghmem unroute` over the same items changes nothing.

    When: `ghmem unroute 1` runs twice, then the mirror is rebuilt from
      the resulting ledger
    Then: the second run still reports one item, and the rebuilt mirror
      holds the same routes as the live one — #2's, and only #2's
    """
    corpus = _corpus(tmp_path)
    _run(monkeypatch, corpus, ["unroute", "1"])

    code, output = _run(monkeypatch, corpus, ["unroute", "1"])

    assert code == 0, output
    assert output.strip() == "Unrouted 1 item(s)"
    store = Store.open(corpus / "cache.sqlite")
    live: list[Route] = store.routes()
    assert [r.item_id for r in live] == [GraphQLId("I_2")]
    assert CliRunner().invoke(main, ["rebuild"]).exit_code == 0
    assert Store.open(corpus / "cache.sqlite").routes() == live
