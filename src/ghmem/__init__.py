"""ghmem: institutional-memory pipeline for a GitHub repository."""


def __getattr__(name: str) -> str:
    """Read `__version__` from the installed metadata on first use.

    `importlib.metadata` costs about 80 ms to import, and `ghmem.hook` runs as
    its own process on every tool call of every session that enables the
    plugin. Importing that module imports this package, so the metadata is
    not read until something asks for the version.
    """
    if name == "__version__":
        from importlib.metadata import version  # noqa: PLC0415 - see above

        return version("ghmem-cli")
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
