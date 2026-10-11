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


def design_dir() -> Path:
    """Return the design-docs directory, creating it if absent.

    Honors ``$GHMEM_DESIGN_DIR`` when set; otherwise resolves to
    ``<git-toplevel>/docs/design``. Created on demand: the first live
    run's lead finding was a command dying on a directory nothing had
    made, so `index` writes into a directory it can rely on rather than
    one a later subproject must remember to scaffold first.
    """
    override = os.environ.get("GHMEM_DESIGN_DIR")
    directory = Path(override) if override else _git_toplevel() / "docs" / "design"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def packet_dir() -> Path:
    """Return the scratch directory for served triage packets, creating it.

    Honors ``$GHMEM_PACKET_DIR``; otherwise
    ``<git-toplevel>/tools/ghmem/tmp/packets``, inside the gitignored
    ``tmp/`` scratch tree the gate's own sentinels already use. One file
    per live session token, overwritten on each `serve_next` call — a
    triage agent routes an item immediately after reading its packet,
    so only the latest copy is ever needed.
    """
    override = os.environ.get("GHMEM_PACKET_DIR")
    directory = (
        Path(override)
        if override
        else _git_toplevel() / "tools" / "ghmem" / "tmp" / "packets"
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def fetch_ledger_path() -> Path:
    """Return the path to the fetch event ledger."""
    return corpus_dir() / "fetch.jsonl"


def routing_ledger_path() -> Path:
    """Return the path to the routing decision ledger."""
    return corpus_dir() / "routing.jsonl"


def sqlite_path() -> Path:
    """Return the path to the SQLite cache/mirror database."""
    return corpus_dir() / "cache.sqlite"
