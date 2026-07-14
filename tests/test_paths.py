"""Test corpus directory path resolution."""

from pathlib import Path

import pytest

from ghmem.paths import corpus_dir


def test_corpus_dir_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """$GHMEM_CORPUS_DIR overrides the default corpus location.

    When: GHMEM_CORPUS_DIR is set to a tmp directory
    Then: corpus_dir() returns that directory, not the git-toplevel default
    """
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(tmp_path))

    assert corpus_dir() == tmp_path
