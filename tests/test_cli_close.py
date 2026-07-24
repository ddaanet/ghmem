"""Test the `ghmem close` CLI command."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ghmem.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "heat_corpus.jsonl"

_ROUTING_JSONL = (
    '{"type": "route", "data": {"session": "s1", "item_id": "I_1", '
    '"verdict": "softwrap", "note": "note 1"}}\n'
    '{"type": "route", "data": {"session": "s1", "item_id": "I_2", '
    '"verdict": "softwrap", "note": "note 2"}}\n'
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


def test_close_clean_regenerates_index_and_recaps(
    corpus: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean close regenerates INDEX.md and prints "closed <topic>".

    When: a synthesized doc for "softwrap" already sits in the design
      dir, `ghmem topic softwrap` mints a snapshot token, and `ghmem
      close <tok>` is invoked with that token
    Then: the command exits 0, stdout is "closed softwrap", and the
      regenerated INDEX.md lists softwrap as synthesized — no longer
      the "*not yet synthesized*" pending marker
    """
    design = tmp_path / "design"
    design.mkdir()
    (design / "softwrap.md").write_text(
        "---\ndescription: line up wrapped text\n---\nSee #1 and #2.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GHMEM_DESIGN_DIR", str(design))

    runner = CliRunner()
    topic_result = runner.invoke(main, ["topic", "softwrap"])
    assert topic_result.exit_code == 0, topic_result.output
    token = topic_result.output.splitlines()[0].removeprefix("snapshot: ")

    result = runner.invoke(main, ["close", token])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "closed softwrap"
    written = (design / "INDEX.md").read_text(encoding="utf-8")
    assert "[softwrap](softwrap.md)" in written
    assert "*not yet synthesized*" not in written


def test_close_unknown_token_exits_one(corpus: Path) -> None:
    """`ghmem close deadbeef` exits 1 with the no-snapshot message on stderr.

    When: TOKEN names no snapshot `ghmem topic` ever minted
    Then: the command exits 1, stdout is empty, and the no-snapshot
      message is on stderr
    """
    result = CliRunner().invoke(main, ["close", "deadbeef"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ghmem close: no snapshot for token 'deadbeef'" in result.stderr
