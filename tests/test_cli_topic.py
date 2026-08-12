"""Test the `ghmem topic` CLI command."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ghmem.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "heat_corpus.jsonl"

_ROUTING_JSONL = (
    '{"type": "route", "data": {"session": "s1", "item_id": "I_1", '
    '"verdict": "softwrap", "note": "note 1", '
    '"item_updated_at": "2026-01-01T00:00:00Z"}}\n'
    '{"type": "route", "data": {"session": "s1", "item_id": "I_2", '
    '"verdict": "softwrap", "note": "note 2", '
    '"item_updated_at": "2026-01-05T00:00:00Z"}}\n'
)


@pytest.fixture
def corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point $GHMEM_CORPUS_DIR at a rebuilt corpus with two items routed."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FIXTURE.read_text())
    (corpus / "routing.jsonl").write_text(_ROUTING_JSONL)
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    assert CliRunner().invoke(main, ["rebuild"]).exit_code == 0
    return corpus


def test_topic_prints_context_and_exits_zero(corpus: Path) -> None:
    """`ghmem topic softwrap` exits 0 and prints the snapshot and item lines.

    When: two items are routed to "softwrap" and the corpus rebuilt
      (routing.jsonl is replayed too, unlike test_cli_index's fetch-only
      fixture)
    Then: the command exits 0, stdout opens with "snapshot: ", and both
      routed items' `#`-lines appear
    """
    result = CliRunner().invoke(main, ["topic", "softwrap"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("snapshot: ")
    assert "#1 " in result.output
    assert "#2 " in result.output


def test_topic_unrouted_name_exits_one(corpus: Path) -> None:
    """`ghmem topic bogus` exits 1 with the not-routed message on stderr.

    When: NAME names a topic nothing was routed to
    Then: the command exits 1, stdout is empty, and the not-routed
      message is on stderr
    """
    result = CliRunner().invoke(main, ["topic", "bogus"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem topic: topic 'bogus' has no routed items" in result.stderr
