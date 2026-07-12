"""Test the `ghmem rebuild` CLI command."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ghmem.aliases import Number
from ghmem.cli import main
from ghmem.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "fetch_small.jsonl"


def test_rebuild_command_populates_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test that `ghmem rebuild` rebuilds the SQLite mirror in place.

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
    assert store.get_item(Number(1)).title == "Softwrap loses column alignment"
