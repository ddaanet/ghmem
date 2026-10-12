"""Corpus isolation shared by every test module.

Two fixtures, and between them no test reads the checkout's own corpus:

- `_no_ambient_corpus` (autouse) points `$GHMEM_CORPUS_DIR` at a
  directory that does not exist, so nothing falls back to
  `<git-toplevel>/corpus` — whose `target.toml` would otherwise answer
  for every test that forgot to bring its own.
- `target_corpus` is the opt-in for a test that reaches sync or
  standing: a tmp corpus holding a `target.toml`.
"""

from pathlib import Path

import pytest

from tests.sync_helpers import TARGET


@pytest.fixture(autouse=True)
def _no_ambient_corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point $GHMEM_CORPUS_DIR away from the checkout's real corpus."""
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(tmp_path / "no-corpus"))


@pytest.fixture
def target_corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point $GHMEM_CORPUS_DIR at a tmp corpus holding only a `target.toml`.

    For the tests that reach sync or standing. A test that needs ledgers there
    too writes them into the returned directory.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    lines = [f'{key} = "{value}"\n' for key, value in TARGET._asdict().items()]
    (corpus / "target.toml").write_text("".join(lines))
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    return corpus
