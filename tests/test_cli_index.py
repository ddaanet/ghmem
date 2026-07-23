"""Test the `ghmem index` CLI command."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ghmem.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "heat_corpus.jsonl"


@pytest.fixture
def corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point $GHMEM_CORPUS_DIR at a rebuilt heat-fixture corpus."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FIXTURE.read_text())
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    assert CliRunner().invoke(main, ["rebuild"]).exit_code == 0
    return corpus


def test_index_writes_file_and_recaps(
    corpus: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ghmem index` writes INDEX.md and signs off with the topic counts.

    When: a synthesized topic doc sits in the design dir
    Then: INDEX.md is written with its line and the recap names the count
    """
    design = tmp_path / "design"
    design.mkdir()
    (design / "ruler.md").write_text(
        "---\ndescription: line up wrapped text\n---\nWe fold #1 here.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GHMEM_DESIGN_DIR", str(design))

    result = CliRunner().invoke(main, ["index"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "INDEX.md: 1 topics (1 synthesized, 0 pending)"
    written = (design / "INDEX.md").read_text(encoding="utf-8")
    assert "- [ruler](ruler.md) — line up wrapped text (#1)" in written
    assert "Do not edit." in written


def test_index_creates_missing_design_dir(
    corpus: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing design dir is created and gets an empty INDEX.md.

    When: $GHMEM_DESIGN_DIR names a directory that does not exist
    Then: the command creates it, writes a header-only INDEX.md, and
      recaps zero topics rather than failing
    """
    design = tmp_path / "absent"
    monkeypatch.setenv("GHMEM_DESIGN_DIR", str(design))

    result = CliRunner().invoke(main, ["index"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "INDEX.md: 0 topics (0 synthesized, 0 pending)"
    assert (design / "INDEX.md").is_file()
