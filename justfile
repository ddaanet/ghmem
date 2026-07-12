# List available recipes
help:
    @just --list --unsorted

# Format code: ruff fixes, ruff format, docformatter
format:
    uv run ruff check -q --fix-only
    uv run ruff format -q
    docformatter src tests

# Format, lint, typecheck, test
green: format
    uv run ruff check -q
    docformatter -c src tests
    uv run mypy
    uv run pytest
