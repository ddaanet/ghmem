"""Test corpus directory path resolution."""

from pathlib import Path

import pytest

from ghmem.paths import corpus_dir, design_dir


def test_corpus_dir_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """$GHMEM_CORPUS_DIR overrides the default corpus location.

    When: GHMEM_CORPUS_DIR is set to a tmp directory
    Then: corpus_dir() returns that directory, not the git-toplevel default
    """
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(tmp_path))

    assert corpus_dir() == tmp_path


def test_design_dir_env_override_and_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """$GHMEM_DESIGN_DIR overrides the location, and the dir is created.

    When: GHMEM_DESIGN_DIR names a directory that does not exist yet
    Then: design_dir() returns it and has created it on the way out
    """
    target = tmp_path / "design"
    monkeypatch.setenv("GHMEM_DESIGN_DIR", str(target))

    assert design_dir() == target
    assert target.is_dir()
