"""The target repository a corpus mirrors, read from beside that corpus.

Which repository `sync` fetches, and whose login standing treats as the
owner, are facts about a corpus rather than about this code. They live
in `target.toml` inside the corpus directory, so `$GHMEM_CORPUS_DIR`
moves them together with the ledgers they describe.

`load_target` is called at first use, by `sync` and by
`build_standing` only. A command that reaches neither never opens the
file, and so runs on a corpus that has none.

Nothing here is cached: the file is three lines, and a process-wide
cache would outlive a change of `$GHMEM_CORPUS_DIR`.
"""

import tomllib
from pathlib import Path
from typing import NamedTuple

from ghmem.exceptions import TargetConfigError
from ghmem.paths import corpus_dir


def load_target() -> Target:
    """Read the corpus's `target.toml`.

    Raises `TargetConfigError`, naming the file and what is wrong with
    it, if the file is absent, is not TOML, or lacks any one key. Every
    key is required of every caller, and none has a default: a target
    silently filled in is a corpus quietly synced from the wrong
    repository.
    """
    path = corpus_dir() / "target.toml"
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise TargetConfigError(path, "the file does not exist") from exc
    except tomllib.TOMLDecodeError as exc:
        raise TargetConfigError(path, f"it is not valid TOML ({exc})") from exc
    return Target(*(_required(path, data, key) for key in Target._fields))


class Target(NamedTuple):
    """One corpus's target: the repository, and its owner's login."""

    owner: str
    """The repository's owning account — `owner` in `owner/repo`."""
    repo: str
    """The repository's name — `repo` in `owner/repo`."""
    owner_login: str
    """The login standing ranks as `Standing.OWNER`.

    Not derivable from `owner`, which may be an organization.
    """


def _required(path: Path, data: dict[str, object], key: str) -> str:
    """Return `data[key]`, which must be a non-empty string."""
    if key not in data:
        raise TargetConfigError(path, f"key `{key}` is missing")
    value = data[key]
    if not isinstance(value, str) or not value:
        raise TargetConfigError(path, f"key `{key}` must be a non-empty string")
    return value
