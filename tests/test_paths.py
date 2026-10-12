"""Test corpus directory path resolution."""

from pathlib import Path

import pytest

from ghmem.paths import corpus_dir, design_dir, packet_dir


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


def test_packet_dir_defaults_under_corpus_dir_and_is_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without $GHMEM_PACKET_DIR, packets live in `<corpus_dir>/packets`.

    When: GHMEM_CORPUS_DIR names a corpus and GHMEM_PACKET_DIR is unset
    Then: packet_dir() is that corpus's `packets/` and has created it
    """
    corpus = tmp_path / "corpus"
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    monkeypatch.delenv("GHMEM_PACKET_DIR", raising=False)

    assert packet_dir() == corpus / "packets"
    assert (corpus / "packets").is_dir()


def test_packet_dir_env_override_and_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """$GHMEM_PACKET_DIR overrides the location, and the dir is created.

    When: GHMEM_PACKET_DIR names a directory that does not exist yet
    Then: packet_dir() returns it and has created it on the way out
    """
    target = tmp_path / "packets"
    monkeypatch.setenv("GHMEM_PACKET_DIR", str(target))

    assert packet_dir() == target
    assert target.is_dir()
