"""Corpus directory and ledger/sqlite path resolution."""

import functools
import os
import subprocess
from pathlib import Path


@functools.cache
def _git_toplevel() -> Path:
    """Return the enclosing git repository's toplevel directory.

    Cached: the toplevel is fixed per process, so the path helpers that
    fan out through `corpus_dir` share one `git` spawn.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
        text=True,
    )
    return Path(result.stdout.strip())


def corpus_dir() -> Path:
    """Return the corpus directory root.

    Honors ``$GHMEM_CORPUS_DIR`` when set; otherwise resolves to
    ``<git-toplevel>/corpus``.
    """
    override = os.environ.get("GHMEM_CORPUS_DIR")
    if override:
        return Path(override)
    return _git_toplevel() / "corpus"


def fetch_ledger_path() -> Path:
    """Return the path to the fetch event ledger."""
    return corpus_dir() / "fetch.jsonl"


def routing_ledger_path() -> Path:
    """Return the path to the routing decision ledger."""
    return corpus_dir() / "routing.jsonl"


def sqlite_path() -> Path:
    """Return the path to the SQLite cache/mirror database."""
    return corpus_dir() / "cache.sqlite"
