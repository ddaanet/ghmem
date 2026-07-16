# List available recipes
help:
    @just --list --unsorted

# Sync deps into .venv; skipped in the sandbox, which can't write uv's cache
sync:
    [ -n "$SANDBOX_RUNTIME" ] || uv sync --quiet

# Format code: ruff fixes, ruff format, docformatter
format: sync
    .venv/bin/ruff check -q --fix-only
    .venv/bin/ruff format -q
    docformatter src tests

# Format, lint, typecheck, test
green: format
    .venv/bin/ruff check -q
    docformatter -c src tests
    .venv/bin/mypy
    .venv/bin/pytest
