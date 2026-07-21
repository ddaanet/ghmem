"""Test the `ghmem heat` CLI command."""

import csv
import io
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


def test_heat_emits_csv_with_a_header(corpus: Path) -> None:
    """Ranked output is CSV, so calibration can read it as data.

    When: `ghmem heat` runs over the fixture corpus
    Then: stdout parses as CSV whose header names every HeatScore field
    """
    result = CliRunner().invoke(main, ["heat"])

    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert list(rows[0]) == [
        "number",
        "title",
        "score",
        "comments",
        "reactions",
        "participants",
        "related_items",
        "span_years",
    ]
    # Ordering itself is test_heat.py's contract: the fixture's heat
    # order coincides with its number order, so asserting the numbers
    # here would pass under a plain ORDER BY number.
    assert [float(row["score"]) for row in rows] == sorted(
        (float(row["score"]) for row in rows), reverse=True
    )


def test_heat_quotes_titles_containing_commas(corpus: Path) -> None:
    """A title with a comma stays one field, not two.

    When: item #1's title is "Hot item, years of back-and-forth"
    Then: the parsed row's title field is that whole string
    """
    result = CliRunner().invoke(main, ["heat"])

    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert rows[0]["title"] == "Hot item, years of back-and-forth"
